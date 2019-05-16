from __future__ import print_function  # OBS behövs denna

from discriminator import Discriminator
from generator import GeneratorWithSkipConnections

import torch
import torch.nn as nn
from torch.optim.lr_scheduler import StepLR
import torch.optim as optim

import os
import random

from torch.utils.data import Dataset, DataLoader
from scipy.misc import imread

from torchvision import transforms, utils
from skimage import io, transform
import numpy as np
from skimage import color

from PIL import Image
import matplotlib.pyplot as plt


def set_seed( seed = 1234 ):
    """ sets a seed for random and torch.
        Default seed: 1234
    """
    random.seed(seed)
    torch.manual_seed(seed)


class SimpsonsDataset(Dataset):
    def __init__(self, datafolder, transform=None, rgb=True):

        self.datafolder = datafolder
        all_files_list = [os.path.join(dp, f) for dp, dn, fn in os.walk(
            os.path.expanduser(datafolder)) for f in fn]

        self.image_files_list = [s for s in all_files_list if
                                 '_gray.jpg' not in s and '.jpg' in s and os.path.isfile(os.path.join(datafolder, s))]
        self.transform = transform
        self.rgb = rgb

    def __len__(self):
        return len(self.image_files_list)

    def __getitem__(self, idx):
        img_name = os.path.join(self.datafolder,
                                self.image_files_list[idx])
        img_name_gray = img_name[0:-4] + '_gray.jpg'
        image = io.imread(img_name)
        image_rgb = image

        if self.rgb:
            # Get the data as RGB
            image_norm = self.transform(image)
            img_name_gray = img_name[0:-4] + '_gray.jpg'
            image_gray = io.imread(img_name_gray)
            image_gray_un_norm = image_gray
            image_gray = self.transform(image_gray)

            return image_norm, image_gray, image_gray_un_norm, image_rgb, img_name, img_name_gray

        else:
            # Get the data as LAB
            image_lab = color.rgb2lab(np.array(image))
            image_l = image_lab[:, :, [0]]
            image_tanh = (image - 127.5) / 127.5
            image_tanh = self.transform(image_tanh)
            image = self.transform(image)
            image_l = self.transform(image_l)
            image_lab = self.transform(image_lab)

            return image, image_l, image_lab, img_name, img_name_gray, image_gray_un_norm, image_rgb


def loadSimpsonDataset( workers, batch_size ):
    """ Creates dataloaders for the training and validation data sets.
    """

    # Paths to the training and validation data sets
    dataroot_train = "/home/projektet/dataset/trainset/"
    dataroot_val = "/home/projektet/dataset/validationset/"

    # Loading the data
    trainset = SimpsonsDataset(datafolder=dataroot_train, transform=transforms.Compose([
        transforms.ToTensor()
    ]), rgb=False)

    validationset = SimpsonsDataset(datafolder=dataroot_val, transform=transforms.Compose([
        transforms.ToTensor()
    ]), rgb=False)

    # Creating the data loaders
    dataloader = torch.utils.data.DataLoader(
        trainset, batch_size=batch_size, shuffle=True, num_workers=workers)

    dataloader_validation = torch.utils.data.DataLoader(
        trainset, batch_size=int(batch_size / 4), shuffle=True, num_workers=workers)

    return dataloader, batch_size, dataloader_validation


def optimizers(generator, discriminator, learningrate=2e-4, amsgrad=False, b=0.5, momentum=0.9):
    """ Returns an optimizer for the discriminator and one for the generator.

        For the generator the Adam (OBS source: https://github.com/soumith/ganhacks) optimizer is used with betas = 0.5, 0.999.
        The first value 0.5 is used instead of the default value according to: https://arxiv.org/pdf/1803.05400.pdf

        For the discriminator SGD (OBS source: https://github.com/soumith/ganhacks) with momentum and weight decay is used. The learning rate is initially set to 2e-4 * 2
        and is decayed every 5th epoch during training.
    """

    Discriminator_optimizer = optim.SGD(
        discriminator.parameters(), lr = 2e-4 * 2, weight_decay = 1e-4, momentum = momentum)

    Generator_optimizer = optim.Adam(
        generator.parameters(), lr = learningrate, betas = (b, 0.999))

    return Discriminator_optimizer, Generator_optimizer


def get_loss_function(BCE=True):
    """ Returns the loss function which is used during the training of the generator and the discriminator.
    """

    if BCE:

        return nn.BCELoss()

    return False



def get_labels():
    """ Labels used for real images and fake images.
    """
    true_im = 1
    false_im = 0

    return true_im, false_im


def patchGan(output, alpha, min_max_patchGan_func):
    """ Performs patchGan on a 1 x 32 x 32 output of the discriminator.
    """

    if patchGan_func:
        output = compute_soft_min_max(output, alpha)
    else:
        output = torch.mean(output, dim=3).mean(dim=2).squeeze(1)

    return output

def uniformly_sample_labels(real_label_upper, real_label_lower, batch_size, device):
    """ Uniformly samples batch_size number of label values within a provided range.
    """
    real_label_upper = 1
    real_label_lower = 0.8

    soft_labels = torch.tensor([random.uniform(real_label_lower, real_label_upper) for _ in range(batch_size)]).to(device)

    return soft_labels

def create_label_vector( image_label , batch_size, device ):
    """ Creates a label vector from an image label.
    """

    return torch.full((batch_size,), image_label, device=device)

def normalize_generator_output( unorm_images ):
    """ Normalizes values within the range [-1,1] to [0,1]
    """

    normalized_image_batch = ((( unorm_images + 1) * (1)) / (2))

    return normalized_image_batch


def compute_L1_loss( rgb_images, generated_rgb_images ):
    """ Computes the L1 loss between a generated and a real image.
    """
    # Transpose the real rgb image to get the right dimensions for the L1 funciton
    rgb_images_transposed = torch.tensor(np.transpose(np.array(rgb_images), (0, 3, 1, 2)))

    # Computes the L1 loss
    L1_loss = L1( generated_rgb_images.view( generated_rgb_images.size(0) , -1), rgb_images_transposed.view( rgb_images_transposed.size(0) , -1).float() )

    return L1_loss


def GAN_training():

    epochs = 100
    ngpu = 1

    # If the code is run on a device with a gpu cuda:0 is set as device, otherwise it is set as cpu.
    device = torch.device("cuda:0" if (torch.cuda.is_available() and ngpu > 0) else "cpu")

    network_v = "network_v8"

    path = "/home/projektet/" + network_v + "/"

    # Create a genenerator and a discriminator
    generator = GeneratorWithSkipConnections().to(device)
    discriminator = Discriminator().to(device)

    # Number of workers for dataloader
    workers = 4

    # Batch size during training
    batch_size = 56

    # Loads two dataloaders. One for the validation and one for the training set.
    dataloader, batch_size, dataloader_validation = loadSimpsonData( workers, batch_size )

    # Get the labels for real and fake image.
    true_im_label, false_im_label = get_labels()


    Discriminator_optimizer, Generator_optimizer = optimizers(generator, discriminator)

    # The a scheduler for the discriminator optimizer is set with a step_size of 5 and a decay parameter of 0.5
    # such that the learning rate is decayed by 0.5 every 5th epoch.
    Discriminator_optimizer_scheduler = StepLR( Discriminator_optimizer, step_size=5, gamma=0.5)

    # Vectors used to store training and validation losses.
    D_losses = []
    D_losses_val = []

    G_losses = []
    G_losses_val = []

    # Set a value for lambda, which is used during the L1 regularization of the generator.
    regularization_lambda = 100 / 255

    # The initial function used in the patch gan at epoch 0 is set.
    initial_epoch = 0
    patchGan_func = update_patch_function(initial_epoch)

    # The mode of the discriminator and generator to training so that
    # the weights (OBS: weights or filters ?) of the network can be updated.
    discriminator = discriminator.train()
    generator = generator.train()

    for epoch in range(initial_epoch, epochs):

        # It is checked if the function used for patchGan should be updated from mean to softmin.
        if update_patch_function(epoch):
            patchGan_func = update_patch_function(epoch)

        # The Discriminator optimizer scheduler is notified that another epoch has passed.
        # The learning rate of the optimizer is only decayed every 5th epoch.
        Discriminator_optimizer_scheduler.step()
        print('Epoch:', epoch, 'LR:', Discriminator_optimizer_scheduler.get_lr())

        # For every epoch we use the dataloader to go trhough one batch at the time
        #for current_batch, ((image_norm, image_gray, image_gray_un_norm, image_rgb, img_name, img_name_gray), (image_norm_val, image_gray_val, image_gray_un_norm_val, image_rgb_val, img_name_val, img_name_gray_val)) in enumerate(zip(dataloader, dataloader_validation)):
        for current_batch,((image, image_l, image_lab, img_name, img_name_gray, image_gray_un_norm, image_rgb), (image_val, image_l_val, image_lab_val, img_name_val, img_name_gray_val, image_gray_un_norm_val, image_rgb_val)) in enumerate(zip(dataloader, dataloader_validation)):

            image = image.to(device)
            image_l = image_l.to(device)
            image_lab = image_lab.to(device)

            # Go from LAB -> [0,1]
            image_normalized_lab = normalize_img_lab(image_lab.float())

            # Go from L -> [0,1]
            image_normalized_l = normalize_img_l(image_l.float())


            """Train the discriminator """

            # 1. Train with a batch of real colored images

            # Clears the gradients of all optimized torch.Tensors
            discriminator.zero_grad()

            # forward pass on the normalized real colored images
            output = discriminator( image_normalized_lab.float() )

            # We use patchgan on the output with softmin -> alpha = -1
            alpha = -1
            output = patchGan(output, alpha, patchGan_func)

            # Using soft labels for the discriminator on real data during training.
            # According to source:    (OBS)
            real_label_upper = 1
            real_label_lower = 0.8

            soft_labels_real_images = uniformly_sample_labels( real_label_upper, real_label_lower, batch_size, device )

            # Get the binary cross entropy loss function.
            BCE_loss = get_loss_function( BCE = True )

            # The loss on the real batch data
            D_loss_real = BCE_loss( output, soft_labels_real_images )

            # Compute gradients for D via a backward pass
            D_loss_real.backward()

            D_x = output.mean().item()


            # 2.Train discriminator with a batch of generated colorized images

            # Generate colorized images from black and white images
            generated_images = generator(image_normalized_l.float() )

            # Change range from [-1, 1] to LAB range
            generated_images_lab = change_range_tanh_to_lab_batch(generated_images)

            # Format labels into tensor vector
            labels_fake = create_label_vector( false_im_label, batch_size, device)

            # The generated images within the range [-1,1] are normalized into the range [0,1]
            normalized_generated_images = normalize_generator_output( generated_images )

            # Train the Discriminator with generated images
            output = discriminator(normalized_generated_images.detach() )

            # We use patchgan on the output with softmax -> alpha = 1
            alpha = 1
            output = patchGan( output, alpha, patchGan_func)

            # The discriminator loss on the generated data is computed
            D_loss_fake = BCE_loss( output, labels_fake )

            # Backward pass for the discriminator on the generated data
            D_loss_fake.backward()

            D_G_x1 = output.mean().item()

            # The total discriminator loss
            D_loss = D_loss_fake + D_loss_real

            # The discriminator is updated a step. Walk a step. - gradient descent
            Discriminator_optimizer.step()


            """Train the generator """

            # Clears the gradients of all optimized torch.Tensors
            generator.zero_grad()

            # Format labels into tensor vector
            labels_real = create_label_vector( true_im_label, batch_size, device)

            # Train the Discriminator with generated images
            output = discriminator(normalized_generated_images)

            # We use patchgan on the output with softmin -> alpha = -1
            alpha = -1
            output = patchGan( output, alpha, patchGan_func)

            # The generator loss on the generated data is computed
            # Note that the generated data is real data for the generator.
            G_loss_bce = BCE_loss( output, labels_real )

            # L1 regularization is used for the generator
            L1 = nn.L1Loss()

            # Compute the L1 loss for the generator between rgb generated images and real rgb images.
            G_loss_L1 = compute_L1_loss( image_lab, generated_images_lab )

            # The total generator loss is computed.
            G_loss = G_loss_bce + regularization_lambda * G_loss_L1

            # The backward pass for the generator is performed.
            G_loss.backward()

            D_G_x2 = output.mean().item()

            # Training of the generator for the current batch is done. Thus we take a step.
            Generator_optimizer.step()

            # The Current losses of the generators and discriminators are saved.
            G_losses.append( G_loss.item() )
            D_losses.append( D_loss.item() )


            """  VALIDATION """

            """ Validation of the discriminator at the current epoch of the current batch"""

            # Set the mode of the discriminator and generator to eval so that the weights of the network are not updated.
            discriminator = discriminator.eval()
            generator = generator.eval()

            # Set the device on the validation images to either cpu or cuda depending on the device.
            image_val = image_val.to(device)
            image_l_val = image_l_val.to(device)
            image_lab_val = image_lab_val.to(device)

            image_gray_un_norm_val = image_gray_un_norm_val.to(device)
            image_rgb_val = image_rgb_val.to(device)

            # Go from LAB -> [0,1]
            image_normalized_lab_val = normalize_img_lab(image_lab_val.float())

            # Go from L -> [0,1]
            image_normalized_l_val = normalize_img_l(image_l_val.float())

            # Forward pass on the normalized real colored images
            output = discriminator( image_normalized_lab_val.float() )

            # We use patchgan on the output with softmin -> alpha = -1
            alpha = -1
            output = patchGan( output, alpha, patchGan_func)

            # Format labels into tensor vector
            labels_real_val = create_label_vector( true_im_label, int(batch_size/4), device)

            # Compute the binary cross entropy loss on real data
            D1_loss_real_val = BCE_loss( output, labels_real_val )

            # Generate colorized images from black and white images
            batch_fake_lab_val = generator( image_normalized_l_val.float() )

            # Change range from [-1, 1] to LAB range
            generated_images_lab_val = change_range_tanh_to_lab_batch(batch_fake_lab_val)

            # Format labels into tensor vector
            labels_fake_val = create_label_vector( false_im_label, int(batch_size / 4), device)

            # The generated images within the range [-1,1] are normalized into the range [0,1]
            normalized_generated_images_lab_val = normalize_generator_output( batch_fake_lab_val )

            # Train the Discriminator with generated images
            output = discriminator( normalized_generated_images_lab_val.detach() )

            # We use patchgan on the output with softmax -> alpha = 1
            alpha = 1
            output = patchGan( output, alpha, patchGan_func)

            # Compute the binary cross entropy loss on fake data
            D1_loss_fake_val = BCE_loss( output, labels_fake_val )

            # Compute the total discriminator loss
            D_loss_val = D1_loss_fake_val + D1_loss_real_val


            """ Validation of the generator at the current epoch of the current batch"""

            # Format labels into tensor vector
            labels_real_val = create_label_vector( true_im_label, int(batch_size / 4), device )

            # Forward pass on the normalized generated colored images
            output = discriminator( normalized_generated_images_lab_val )

            # We use patchgan on the output with softmin -> alpha = -1
            alpha = -1
            output = patchGan( output, alpha, patchGan_func)

            # The generators loss
            G1_loss_bce_val = BCE_loss( output, labels_real_val )

            # The L1 loss function
            L1_val = nn.L1Loss()

            # Compute the L1 loss for the generator between rgb generated images and real rgb images.
            G_loss_L1 = compute_L1_loss( image_lab_val, generated_images_lab_val )

            # Compute the regularized generator loss
            G_loss_val = G1_loss_bce_val + regularization_lambda * G_loss_L1_val

            # store the validation losses
            G_losses_val.append( G_loss_val.item() )
            D_losses_val.append( D_loss_val.item() )


            if current_batch % 50 == 0:
                # To follow the performance of the generator and discriminator losses from training and vlaidation are printed every 50th epoch.
                print('[%d/%d][%d/%d]\tLoss_D: %.4f\tLoss_G: %.4f\tLoss_val_D: %.4f\tLoss_val_G: %.4f\tD(x): %.4f\tD(G(z)): %.4f / %.4f' %
                      (epoch, epochs, current_batch, len(dataloader), D_loss.item(), G_loss.item(), D_loss_val.item(), G_loss_val.item(),  D_x, D_G_x1, D_G_x2))

                # The losses on the training and validation data is plotted every 50th epoch.
                plot_losses(path, G_losses, D_losses, G_losses_val, D_losses_val, epoch, current_batch)

            if current_batch % 100 == 0:
                # Qualatively validate the generators performance every 100th batch by generating a image from the generator.
                image_index = -1
                # For reference the corresponding gray scale and rgb image is saved
                save_image( path, image_gray_un_norm_val[image_index], epoch, current_batch, device, False)
                save_image( path, image_rgb_val[image_index], epoch, current_batch, device, True)

                # Change the dimension of the image used for validation
                wrapped_bw_im = image_gray_val[image_index].unsqueeze(0).to(device)

                validate_generator(path + "result_pics/", epoch, current_batch, wrapped_bw_im, generator)

            if current_batch == 0:
                # Save the generator and discriminator models.
                save_model( "generator_model", path, current_batch, generator, epoch)
                save_model( "discriminator_model", path, current_batch, discriminator, epoch)

                # Save the generator and discriminator optimizers.
                save_model( "Discriminator_optimizer", path, current_batch, Discriminator_optimizer, epoch)
                save_model( "Generator_optimizer", path, current_batch, Generator_optimizer, epoch)

                # Save the generator and discriminator training and validation vectors.
                np.save(path + 'G_losses.npy', G_losses)
                np.save(path + 'D_losses.npy', D_losses)
                np.save(path + 'G_losses_val.npy', G_losses_val)
                np.save(path + 'D_losses_val.npy', D_losses_val)


            # The current epoch is finished.
            # Set the mode of the discriminator and generator to training
            discriminator = discriminator.train()
            generator = generator.train()


def save_model( file_name, path, current_batch, model, epoch ):

    file_path = path + "models/" + file_name + "_" + str(current_batch) + "_" + str(epoch) + ".pt"
    torch.save(model.state_dict(), file_path )


def validate_generator(save_path, epoch, current_batch, bw_im, generator):
    """ Generates a colored image and saves it
    """

    with torch.no_grad():

        generated_image = generator(bw_im).detach().cpu()

        generated_image = change_range_tanh_to_ab(generated_image)

        generated_image = change_range_lab_to_rgb(generated_image.to(device))

        # Save the image
        image = Image.fromarray(image, 'RGB')
        image_path = save_path + str(epoch) + "_" + str(current_batch) + "_generated.png"
        image.save( image_path )


def change_range_tanh_to_ab(unorm_image):
    """ Change range values within the range [-1,1] to LAB range for one image
    """

    #LAB color range
    l_lower = 0
    l_upper = 100
    a_lower = -86.125
    a_upper = 98.254
    b_lower = -107.863
    b_upper = 94.482

    # Convert to numpy
    unorm_image = unorm_image.data.numpy()

    # Change range
    l = l_lower +  (unorm_image[0,0,:,:] - (-1)) * (l_upper-l_lower) / (1 - (-1))
    a = a_lower +  (unorm_image[0,1,:,:] - (-1)) * (a_upper-a_lower) / (1 - (-1))
    b = b_lower +  (unorm_image[0,2,:,:] - (-1)) * (b_upper-b_lower) / (1 - (-1))

    # Comcatenate
    image_lab = torch.tensor([l, a, b])

    return image_lab


def change_range_tanh_to_lab_batch(unorm_images):
    """ Change range values within the range [-1,1] to LAB range for one batch
    """

    #LAB color range
    l_lower = 0
    l_upper = 100
    a_lower = -86.125
    a_upper = 98.254
    b_lower = -107.863
    b_upper = 94.482

    # Convert to numpy
    unorm_image = unorm_images.data.numpy()

    # Change range
    l = torch.tensor(l_lower +  (unorm_image[:,0,:,:] - (-1)) * (l_upper-l_lower) / (1 - (-1))).unsqueeze(1) #).astype(int)
    a = torch.tensor(a_lower +  (unorm_image[:,1,:,:] - (-1)) * (a_upper-a_lower) / (1 - (-1))).unsqueeze(1) #).astype(int)
    b = torch.tensor(b_lower +  (unorm_image[:,2,:,:] - (-1)) * (b_upper-b_lower) / (1 - (-1))).unsqueeze(1) #).astype(int)

    # Concatenate
    image_la = torch.cat((l, a), 1).cpu()
    image_lab = torch.cat((image_la, b), 1).cpu()

    return image_lab


def normalize_img_lab(image_in):
    """ Change so that the input image (which is in LAB space) is in [0,1] instead of [LAB]
    """

    #LAB Range
    l_lower = 0
    l_upper = 100
    a_lower = -86.125
    a_upper = 98.254
    b_lower = -107.863
    b_upper = 94.482

    NewMax = 1
    NewMin = 0

    #Convert to CPU & numpy
    image_in = image_in.data.cpu().numpy()

    l = torch.tensor(NewMin  +  ((image_in[:,0,:,:] - l_lower) * (NewMax-NewMin) / (l_upper - l_lower))).unsqueeze(1)
    a = torch.tensor(NewMin  +  ((image_in[:,1,:,:] - a_lower) * (NewMax-NewMin) / (a_upper - a_lower))).unsqueeze(1)
    b = torch.tensor(NewMin  +  ((image_in[:,2,:,:] - b_lower) * (NewMax-NewMin) / (b_upper - b_lower))).unsqueeze(1)

    image_la_norm = torch.cat((l, a), 1).cpu()
    image_lab_norm = torch.cat((image_la, b), 1).cpu()

    return image_lab_norm

def normalize_img_l(image_in):
    """ Change so that the input image (which is in L space) is in [0,1] instead of [LAB]
    """
    #LAB range
    l_lower = 0
    l_upper = 100

    NewMax = 1
    NewMin = 0

    image_in = image_in.data.cpu().numpy()
    l_norm = torch.tensor(NewMin  +  ((image_in[:,:,:,:] - l_lower) * (NewMax-NewMin) / (l_upper - l_lower))) #).astype(int)

    return l_norm


def preprocess_image( image ):

    image = image.squeeze(0)
    image = np.transpose( image, (1, 2, 0))
    image = np.array( image )
    image = image.astype( np.uint8 )

    return image


def save_image(path, img, epoch, current_batch, device, RGB_image):
    """ Saves either a rgb or a gray scale image.
    """
    if( RGB_image ):

        img = Image.fromarray(np.array(img.detach().cpu()))
        image_path = path + "result_pics/" + str(epoch) + "_" + str(current_batch) + "_color.png"
        img.save( image_path )

    else: # If a gray scale image

        img = Image.fromarray(np.array(img.detach().cpu()).astype(np.uint8))
        image_path = path + "result_pics/" + str(epoch) + "_" + str(current_batch) + "_BW.png"
        img.save( image_path )


def plot_losses( path, G_losses, D_losses, G_losses_val, D_losses_val, epoch, current_batch ):
    """ Creates two plots. One for the Generator training and validation losses and one for the
        Discriminator training and validation losses and then saves these figures.
    """

    # Plot the discriminator training and validation losses.
    D_loss_fig = plt.figure('D_cost' + str(epoch) + '_' + str(current_batch))
    plt.plot(D_losses, color='b', linewidth=1.5, label='D cost training')
    plt.plot(D_losses_val, color='purple', linewidth=1.5,label='D cost validation')
    plt.legend(loc='upper left')
    D_loss_fig.savefig(path + 'plots/' + 'D_cost.png', dpi=D_loss_fig.dpi)
    plt.close(D_loss_fig)

    # Plot the generator training and validation losses.
    G_loss_fig = plt.figure('G cost' + str(epoch) + '_' + str(current_batch))
    plt.plot(G_losses, color='b', linewidth=1.5, label='G cost training')
    plt.plot(G_losses_val, color='purple', linewidth=1.5, label='G cost validation')
    plt.legend(loc='upper left')
    G_loss_fig.savefig(path + 'plots/' + 'G_cost.png', dpi=G_loss_fig.dpi)
    plt.close(G_loss_fig)


def update_patch_function(epoch, min=False):
    """ Initially we want to use the mean when we calculate the patch loss. But as the model stabalizes we want
        start focusing on the bad areas created by the generator. Thus we instead switch to a mean function.
    """
    if epoch > 22:
        min_func = True
        return min_func

    else:
        return False


if __name__ == "__main__":
    GAN_training()
