import torch.nn as nn
import torch
import kornia.augmentation as K  # augmentaiton
from transformers import CLIPFeatureExtractor, CLIPTextModel, CLIPTokenizer, TrainingArguments
from diffusers import AutoencoderKL, DDPMScheduler, PNDMScheduler, DDIMScheduler, StableDiffusionPipeline, UNet2DConditionModel
import warnings
import json
import subprocess
import wandb
import pandas as pd
from tqdm.auto import tqdm
from PIL import Image
from diffusers.pipelines.stable_diffusion import StableDiffusionSafetyChecker
from diffusers.optimization import get_scheduler
from diffusers.hub_utils import init_git_repo, push_to_hub
from accelerate.utils import set_seed
from accelerate.logging import get_logger
from accelerate import Accelerator
import PIL
import torchvision
from torch.utils.data import Dataset
import torch.utils.checkpoint
import torch.nn.functional as F
import numpy as np
import random
import os
import math
import itertools
import argparse
from huggingface_hub import login
login("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")

warnings.filterwarnings("ignore")

os.environ["WANDB_SILENT"] = "true"  # mute wandb run message
os.environ["WANDB_API_KEY"] = "16d21dc747a6f33247f1e9c96895d4ffa5ea0b27"
parser = argparse.ArgumentParser()
parser.add_argument(
    "--device", help="GPU device number, e.g. cuda:0", default="cuda:0")
parser.add_argument("--version", type=str,
                    help="wandb model version, e.g. v1", required=True)
parser.add_argument("--run_id", type=str,
                    help="wandb run id of model", required=True)
parser.add_argument("--data_root", default="./book dataset",
                    help="path to read csv files")
parser.add_argument("--batch_size", default=2, type=int,
                    help="Generation batch size. For a GPU with 16gb memory, 4 is maximum.")
# parser.add_argument("--calc_fid",default=False,help="whether to generate and save more images for FID score evaluation")
parser.add_argument(
    "--mode", choices=["default,calc_fid,enter_prompt"], help="default mode will generate from ")
parser.add_argument("--num_imgs", type=int, default=4000,
                    help="number of images to generate for computing FID score. Only to be specified if save_for_fid is True")
parser.add_argument('--save_dir', type=str, default="./Output_images",
                    help="Output dir for generated images.")
parser.add_argument("--delete_model", type=bool, default=True,
                    help="whether to delete downloaded model artifact to save storage")
parser.add_argument("--img_size", type=int, default=512,
                    help="Size of generated image")
parser.add_argument("--fid_stats_path", type=str, default="../book dataset/fid_stats.npz",
                    help="path or compressed numpy file calculated from the original dataset")
parser.add_argument("--prompt", type=str,
                    help="Only to be specified in enter_prompt mode")
args = parser.parse_args()


def image_grid(imgs, rows, cols):
    assert len(imgs) == rows*cols

    w, h = imgs[0].size
    grid = Image.new('RGB', size=(cols*w, rows*h))
    grid_w, grid_h = grid.size

    for i, img in enumerate(imgs):
        grid.paste(img, box=(i % cols*w, i//cols*h))
    return grid

# For reproducibility


def set_seed(seed: int = 42) -> None:
    np.random.seed(seed)
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    # When running on the CuDNN backend, two further options must be set
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    # Set a fixed value for the hash seed
    os.environ["PYTHONHASHSEED"] = str(seed)
    print(f"Random seed set as {seed}")


global_seed = 42
set_seed(global_seed)

# pretrained_model_name_or_path = "runwayml/stable-diffusion-v1-5" #@param {type:"string"}

# Setup the prompt templates for training
training_templates = [  # the first entry is for "highly legible text"
    "A {} book cover with author: {}, book title: {} ",
    # repeat some prompts to give model prior knowledge about book cover styles
    "A {} book cover written by author: {} with book title: {} ",
    #     "A {} simple book cover with author {}, book title {} ",
    #     "A plain {} book cover with author {}. The book title is{} ",
    #     "A {} vivid book cover with author {}, book title {} ",
    "A  {} book cover with author name: {}, book title: {}",
    # #     "We??are??going??to??create??a??clear, {}, highly detailed book??cover??with??author??named??{},??and??book??title??is??'{}'",
    #     "An intricate {}, book??cover??including??book??author:{},??book??title:??'{}'",
    #     "A detailed, {}, book cover with {} ,written by author {}",
    #     "A creative, colorful {}, book cover written by {}. The book title is {}, ",
    #     "A {} old-fashioned, plain book cover written by {}. The book title is {}",
    #     "A simple, {}, old-fashioned book cover with author name {}, book title {} ",
    #     "A simple, {}, plain book cover with author name {}, book title {} ",
    "A detailed {} book cover with author: {} and book title: {} "

]
# TODO: add more to match the number of templates
summary_placeholders = [
    ', and abstract {}',
    ",summary {}}",
    ", the book describes that {}",
    ", book discription {}",
    ", main story {}",
    ", the book is mainly about {}",
    ", and main story {}",
    "and book abstract {}",
    ", and book description {}"
]
test_templates = [  # the first entry is for "highly legible text"
    "A {} book cover with author: {}, book title: {} ",
    # repeat some prompts to give model prior knowledge about book cover styles
    "A {} book cover written by author: {} with book title: {} ",
    "A {} simple book cover with author: {}, book title: {} ",
    "A {} vivid, fantastic book cover with author: {}, book title: {} ",
    #     "We??are??going??to??create??a??clear, {}, highly detailed book??cover??with??author??named??{},??and??book??title??is??'{}'",
    "An intricate {}, book cover including book author:{}, book title: '{}'",
    "A detailed, {}, book cover written by author: {}, with title:{}",
    "A creative, colorful {}, book cover written by: {}. The book title is: {}, ",
    "A {} old-fashioned, plain book cover written by: {}. The book title is: {}",
    "A simple, {}, old-fashioned book cover with author name: {}, book title: {} ",
    "A cartoon-styled, entertaining book cover with author name: {}, book title: {}"
]


# pad to the same length
for i in range(len(summary_placeholders), len(test_templates)):
    summary_placeholders += [random.choice(summary_placeholders)]
summary_placeholders = summary_placeholders[:len(test_templates)]


def get_fid_images(
    pipeline: StableDiffusionPipeline,
    save_dir: str
):
    index = 0
    num_generated = len(os.listdir(save_dir))
    df = pd.read_csv(args.data_root+"/df_test.csv").iloc[num_generated:]
    print(f"{num_generated} images already generated. Skipping them... ")

    while index < args.num_imgs-num_generated:
        torch.cuda.empty_cache()

        rows = df.iloc[index:index+args.batch_size]
        # legible_text is a place_holder
        name, legible_text, author, title = (
            rows[df.columns[0]], "", rows['book_authors'], rows['book_title'])
        prompt = []
        for i in range(len(rows)):
            prompt += [random.choice(training_templates).format(
                legible_text, str(author.iloc[i]), str(title.iloc[i]))]
        # print(prompt)
        try:
            images = pipeline(prompt, height=args.img_size, width=args.img_size,
                              num_inference_steps=50, guidance_scale=7.5).images
            for idx, img in enumerate(images):
                # be careful here, name.iloc[idx] is of type numpy.int64
                img.save(os.path.join(save_dir, str(name.iloc[idx])+'.jpg'))
        except Exception as e:
            print("Encountered exception, skipping this iteration")
            print(f"The problematic prompts are {prompt} ")
        # increment index
        index += args.batch_size
    print("________________________________________")
    print(
        f"Finished generating {args.num_imgs-num_generated} images! Nice job, GPU! (pat it)")


# fix random seed by fixing latents
latents = None


def visualize_prompts(
    pipeline: StableDiffusionPipeline,
    summerize=False,
    include_desc=False,
    max_length=15,
    legible_prompt=True,
    samples_per_prompt=4,
    img_size=args.img_size,
    inference_steps=75,
    save_to_drive=False,
    save_dir=None,
    batch_generate=True
):
    """
    Visualizes the output of the given StableDiffusionPipeline for the given test prompts.
    Args:
    - pipeline: an instance of StableDiffusionPipeline
    - summerize (bool, optional): Whether to summerize the book description. Default is False.
    - include_desc (bool, optional): Whether to include the book description in the prompt. Default is False.
    - max_length (int, optional): The maximum length of the summerized description. Only used when summerize=True.
    - legible_prompt (bool, optional): Whether to add "legible text" to the prompt. Default is True.
    - samples_per_prompt (int, optional): The number of samples to generate for each prompt. Default is 4.
    - img_size (int, optional): The output image size. Default is 512.
    - inference_steps (int, optional): The number of denoising steps. The bigger the less noisy. Default is 75.
    - save_to_drive (bool, optional): Whether to save the generated images to Google Drive. Default is False.
    - save_dir (str, optional): The path to the directory where the generated images should be saved. Only used if save_to_drive=True.
    - batch_generate (bool, optional): Whether to speed up generation by generating in batches. Default is True.

    Returns:
    - None

    Note:
    - If include_desc is True, batch_generate will be set to False because passing stacked descriptions of different length to the model will cause an error.

    Examples:
    ```
    pipeline = StableDiffusionPipeline()
    visualize_prompts(pipeline)
    ```
    This will generate and display 3 samples for each of the test prompts using the default settings.
    ```
    visualize_prompts(pipeline, summerize=True, include_desc=True, max_length=20)
    ```
    This will generate and display 3 samples for each of the test prompts, where each prompt includes a summerized book description with a maximum length of 20.
    ```
    visualize_prompts(pipeline, save_to_drive=True, save_dir="generated_images")
    ```
    This will generate and save 3 samples for each of the test prompts to the Google Drive directory "generated_images".
    ```
    visualize_prompts(pipeline, summerize=True, include_desc=True, max_length=20, batch_generate=False)
    ```
    This will generate and display 3 samples for each of the test prompts, where each prompt includes a summerized book description with a maximum length of 20. The images will be generated one by one instead of in batches.
    """

    if summerize == True:
        assert include_desc == True, "include_desc is False, \
      no summerization can be done without book description!"
    if summerize == False and include_desc == True:
        batch_generate = False
        print("Setting batch_generate to false since adding description without summerizing will cause batch tensors to have different length. This is probably a bug.")
    assert save_dir and save_to_drive and os.path.isdir(
        save_dir), "Must specify save_to_drive=True and save_dir with a valid dir"
    import gc
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.memory_allocated()

    # fix random seed by fixing latents.
    # generate fixed latents if no latents exist
    global latents
    if latents == None or latents.shape[0] != samples_per_prompt:
        generator = torch.Generator(device=args.device)
        generator = generator.manual_seed(global_seed)
        latents = torch.zeros(samples_per_prompt,
                              pipeline.unet.in_channels, img_size // 8, img_size // 8)
        for j in range(samples_per_prompt):
            latents[j, :, :, :] = torch.randn(
                (pipeline.unet.in_channels, img_size // 8, img_size // 8),
                generator=generator,
                device=args.device
            )

    import matplotlib.pyplot as plt
    import random
    # generate from test prompts only
    df = pd.read_csv(args.data_root+"/df_test.csv")

    # set up figures
    dpi = plt.figure().dpi
    fig, axes = plt.subplots(len(test_templates),
                             samples_per_prompt,
                             figsize=(img_size/dpi*samples_per_prompt,
                                      img_size/dpi*len(test_templates))
                             )
    # combind with axes[i][j].set_aspect('auto'); remove spacing
    fig.subplots_adjust(wspace=0, hspace=0)

    # fix random seed by fixing latents
    if include_desc:
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
        tokenizer = AutoTokenizer.from_pretrained(
            "sshleifer/distilbart-cnn-12-6")
        if summerize:
            model = AutoModelForSeq2SeqLM.from_pretrained(
                "sshleifer/distilbart-cnn-12-6")

    # Get model output
    for i in range(len(test_templates)):
        text = []
        # Generate prompts
        for j in range(samples_per_prompt):
            row = df.iloc[j]
            legible_text, author, title, description = (
                "", row['book_authors'], row['book_title'], row['book_desc'])

            if legible_prompt:
                legible_text = "legible text"
            if summerize:
                torch.cuda.empty_cache()
                inputs = tokenizer(description, max_length=1024,
                                   return_tensors="pt", truncation=True, padding="max_length")
                summary_ids = model.generate(inputs['input_ids'], num_beams=3,
                                             min_length=2, max_length=max_length)
                description = tokenizer.batch_decode(summary_ids, skip_special_tokens=True,
                                                     clean_up_tokenization_spaces=False)[0]  # batch_decode returns a list of strings; here len(list)=1, only one input string
                del summary_ids, inputs
                torch.cuda.empty_cache()

            template = test_templates[i]
            if include_desc:
                # append new prompt to list
                template += summary_placeholders[i]
                template = template.format(
                    legible_text, author, title, description)
                text += [template]
            else:
                text += [template.format(legible_text, author, title)]

        # inference
        from torch import autocast
        images = []
        print(f"Inference iteration {i}")

        # free memory before inference (hope this works)
        with autocast("cuda"), torch.no_grad():
            if batch_generate:  # batch generation
                index = 0
                while index < len(text):
                    images += pipeline(text[index:index+args.batch_size], height=img_size, width=img_size,
                                       num_inference_steps=50, guidance_scale=7.5,
                                       latents=latents[index:index+args.batch_size]).images
                    index = index+args.batch_size
            else:  # To avoid out of memory, generate one at a time
                torch.cuda.empty_cache()
                for j in range(samples_per_prompt):
                    images += pipeline(text[j], height=img_size,
                                       width=img_size, num_inference_steps=inference_steps,
                                       guidance_scale=7.5, latents=latents[None, j]).images

        try:
            axes[i][0].set_title(
                f"Prompt {i}, legible={legible_prompt},summerize={summerize},include_desc={include_desc}")
            for j in range(samples_per_prompt):
                axes[i][j].imshow(images[j])
                axes[i][j].set_aspect('auto')  # remove spacing
        # single plot case
        except:
            axes[i].set_title(
                f"Prompt {i}, legible={legible_prompt},summerize={summerize},include_desc={include_desc}")
            # debug
            print(images[0])
            print("images:", images)
            axes[i].imshow(images[0])
            axes[i].set_aspect('auto')

    if save_to_drive:
        # save fig with paramters
        img_name = f"summerize={summerize},\
                include_desc={include_desc}.png"
        path = os.path.join(save_dir, img_name)
        plt.savefig(path)
        fig.show()

    # save checkpoint generation results in wandb
    img_path = "checkpoint_image_sample.jpg"
    plt.savefig(img_path)
    fig.show()
    image = Image.open(img_path)
    wandb.log({"examples": wandb.Image(image)})
    subprocess.run(["rm", "checkpoint_image_sample.jpg"])


noise_scheduler = DDIMScheduler(beta_start=0.00085, beta_end=0.012,
                                beta_schedule="scaled_linear", clip_sample=False, set_alpha_to_one=False)
if args.version != "v0":  # v0 is pretrained model
    # Fine tune result evaluation
    model_name = "stable_diffusion_model:"+args.version
    run_id = args.run_id

    # load from wandb checkpoint
    wandb.init(project="book_cover_generation", id=run_id, name="stable_diffusion " +
               model_name.split(":")[-1]+"+inference", resume='must')
    my_model_artifact = wandb.run.use_artifact(model_name)
    # Download model weights to a folder and return the path
    model_dir = my_model_artifact.download()

    pipeline = StableDiffusionPipeline.from_pretrained(
        model_dir,
        torch_dtype=torch.float16,
        safety_checker=None,
        scheduler=noise_scheduler,
    ).to(args.device)

    # delete downloaded model to save disk space
    if args.delete_model:
        subprocess.run(["rm", "-r", "artifacts"])
    print("------------------------------------------")
    print(f'Load {model_name} from wandb cloud checkpoint')

else:  # version==v0, download pretrained model from huggingface
    print('Load pretrained model from huggingface')
    model_id = "runwayml/stable-diffusion-v1-5"
    model_name = "stable_diffusion_model:v0"
    pipeline = StableDiffusionPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
        revision="fp16",
        safety_checker=None,
        scheduler=noise_scheduler,
    ).to(args.device)

if os.path.isdir(args.data_root+"/"+model_name.split(":")[-1]+" inference"):
    print("Save dir already exists.")


save_dir = args.save_dir+"/"+model_name.split(":")[-1]+" inference"
os.makedirs(save_dir, exist_ok=True)
print(f"Output will be saved in {save_dir}")
print(f"model running on device {args.device}")


# inference
if args.mode == "calc_fid":
    print("Generating images on the test set to compute FID score......")
    get_fid_images(pipeline, save_dir)
    if not os.path.isfile(args.fid_stats_path):
        print(
            f"FID precalculated stats doesn't exist at {args.fid_stats_path}! Exiting program....")
        exit()
    import fid
    paths = (save_dir, args.fid_stats_path)
    fid_score = fid.calculate_fid_given_paths(paths, None)
    print(f"FID score: {fid_score}")
    with open(os.path.join(save_dir, "fid_score.txt"), "w") as f:
        f.write(f"FID score: {fid_score}")

elif mode == "default":
    print("Generating a few images for quick comparison........")
    visualize_prompts(pipeline, summerize=False, samples_per_prompt=4,
                      include_desc=False, legible_prompt=False,
                      batch_generate=True, save_to_drive=True,
                      save_dir=save_dir)

    visualize_prompts(pipeline, summerize=True, include_desc=True,
                      samples_per_prompt=4,
                      legible_prompt=False, save_to_drive=True,
                      save_dir=save_dir)

    visualize_prompts(pipeline, summerize=False, samples_per_prompt=4,
                      include_desc=True, legible_prompt=False,
                      batch_generate=True, save_to_drive=True,
                      save_dir=save_dir)

    # save training hyperparameters to json
    json.dump(dict(wandb.run.config), open(os.path.join(
        save_dir, "hyperparams.json"), "w"), indent=4)

    # Just for fun
    import gc
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.memory_allocated()

    from torch import autocast
    # prompt = "a grafitti in a wall with a <cat-toy> on it" #@param {type:"string"}
    prompt = "Clear, highly detailed book cover with title: Badger's love story and author: Wenxuan Tan"
    # prompt="Clear, highly detailed book cover with description "+book_df.loc[7202]['book_desc']

    num_cols = 2  # @param {type:"number"}
    num_rows = 2  # @param {type:"number"}
    all_images = []

    for _ in range(num_rows):
        with autocast("cuda"):
            # batch generation
            for i in range(num_rows):
                images = pipeline([prompt]*num_cols, height=args.height,
                                  width=args.height, num_inference_steps=50, guidance_scale=7.5).images
                all_images.extend(images)
    grid = image_grid(all_images, num_rows, num_cols)
    wandb.log({"For_fun": wandb.Image(grid)})
elif mode == "enter_prompt":
    image = pipeline(args.prompt, height=args.height, width=args.width,
                      num_inference_steps=50, guidance_scale=7.5).images
    Image.fromarray(image).save(os.path.join(args.save_dir,args.prompt+".jpg"))    
    print("Image saved at ",args.save_dir)
