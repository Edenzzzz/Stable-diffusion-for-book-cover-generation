# Stable diffusion for book cover generation

## Results 
(using simple prompts; fixed latent code and random seed; will be improved soon as I fine-tune text encoder and Unet together on more data for more epochs)
- Preliminary results show that the model cannot render text even after fine-tuning, showing that the latent space is not disentangled regarding text/objects to render.
- Training Unet(the denoising component) improve face and background rendering
- Training the text encoder let the model better captures the concept of book cover from text.
- FID score might be improved when training Unet and text encoder together (now it deteriorates during training). It’s also possible that the model transferred “knowledge” not contained in the training set. 
- Future work should focus on decorrelating the latent codes. A possible solution is the ECCV 2022 best paper about Partial Distance correlations.

**1. Original model (Stable Diffusion V1.5)**
![image](https://user-images.githubusercontent.com/87317405/209904767-8c74d1c0-e7d8-4145-bade-c3a51cf7721c.png)

**2. Train Unet, 8 epochs, 3000 images**
![image](https://user-images.githubusercontent.com/87317405/209904785-56de384f-5b2f-4c87-9c5c-8e9572986427.png)

**3.Train text encoder, 2 epochs, 500 images:**
![image](https://user-images.githubusercontent.com/87317405/209904830-ddfe2481-cb29-472a-be28-ffad10967316.png)

**4. Train text encoder, 8 epochs, 5000 images**
![image](https://user-images.githubusercontent.com/87317405/209904954-a726502c-6f1a-46d3-8c25-1cdb7c0ebc67.png)

**5.Train text encoder, 2 epochs, 6000 images**
![image](https://user-images..com/87317405/209907410-0fff405c-2628-4f35-8711-5dfa06c3cfd2.png)

##TODO
```
Fix distributed training
Test different lr (5e-6 or 5e-5); train Unet and text encoder together
```


