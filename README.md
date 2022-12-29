# Stable-diffusion-for-book-cover-generation
## In this project, I fine-tuned Stable Diffusion on Goodread's best books dataset to test the model's transfer learning ability. 
Preliminary results show that the model cannot render text after fine-tuning, showing that the latent space is not disentangled regarding text/objects to render.

## Results (using simple prompts; fixed latent code and random seed; will be improved soon as I fine-tune text encoder and Unet together on more data for more epochs)

1. Original model (Stable Diffusion V1.5)
![image](https://user-images.githubusercontent.com/87317405/209904767-8c74d1c0-e7d8-4145-bade-c3a51cf7721c.png)

2. Train Unet, 8 epochs, 3000 images
![image](https://user-images.githubusercontent.com/87317405/209904785-56de384f-5b2f-4c87-9c5c-8e9572986427.png)

3.Train text encoder, 2 epochs, 500 images:
![image](https://user-images.githubusercontent.com/87317405/209904830-ddfe2481-cb29-472a-be28-ffad10967316.png)

4. Train text encoder, 8 epochs, 5000 images
![image](https://user-images.githubusercontent.com/87317405/209904954-a726502c-6f1a-46d3-8c25-1cdb7c0ebc67.png)

5.Train text encoder, 2 epochs, 6000 images
![image](https://user-images.githubusercontent.com/87317405/209907410-0fff405c-2628-4f35-8711-5dfa06c3cfd2.png)