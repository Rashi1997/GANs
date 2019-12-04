import tensorflow as tf
from tensorflow.keras import Model
from tensorflow.keras.layers import Dense, Flatten, Conv2D, BatchNormalization, LeakyReLU, Reshape, Conv2DTranspose
from preprocess import load_image_batch
import tensorflow_gan as tfgan
import tensorflow_hub as hub

import numpy as np

from imageio import imwrite
import os
import argparse

# Killing optional CPU driver warnings
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

gpu_available = tf.test.is_gpu_available()
print("GPU Available: ", gpu_available)

## --------------------------------------------------------------------------------------

parser = argparse.ArgumentParser(description='DCGAN')

parser.add_argument('--img-dir', type=str, default='./data/celebA',
                    help='Data where training images live')

parser.add_argument('--out-dir', type=str, default='./output',
                    help='Data where sampled output images will be written')

parser.add_argument('--mode', type=str, default='train',
                    help='Can be "train" or "test"')

parser.add_argument('--restore-checkpoint', action='store_true',
                    help='Use this flag if you want to resuming training from a previously-saved checkpoint')

parser.add_argument('--z-dim', type=int, default=100,
                    help='Dimensionality of the latent space')

parser.add_argument('--batch-size', type=int, default=128,
                    help='Sizes of image batches fed through the network')

parser.add_argument('--num-data-threads', type=int, default=2,
                    help='Number of threads to use when loading & pre-processing training images')

parser.add_argument('--num-epochs', type=int, default=10,
                    help='Number of passes through the training data to make before stopping')

parser.add_argument('--learn-rate', type=float, default=0.0002,
                    help='Learning rate for Adam optimizer')

parser.add_argument('--beta1', type=float, default=0.5,
                    help='"beta1" parameter for Adam optimizer')

parser.add_argument('--num-gen-updates', type=int, default=2,
                    help='Number of generator updates per discriminator update')

parser.add_argument('--log-every', type=int, default=7,
                    help='Print losses after every [this many] training iterations')

parser.add_argument('--save-every', type=int, default=500,
                    help='Save the state of the network after every [this many] training iterations')

parser.add_argument('--device', type=str, default='GPU:0' if gpu_available else 'CPU:0',
                    help='specific the device of computation eg. CPU:0, GPU:0, GPU:1, GPU:2, ... ')

args = parser.parse_args()

## --------------------------------------------------------------------------------------

# Numerically stable logarithm function
def log(x):
    """
    Finds the stable log of x

    :param x: 
    """
    return tf.math.log(tf.maximum(x, 1e-5))

## --------------------------------------------------------------------------------------

# For evaluating the quality of generated images
# Frechet Inception Distance measures how similar the generated images are to the real ones
# https://nealjean.com/ml/frechet-inception-distance/
# Lower is better
module = tf.keras.Sequential([hub.KerasLayer("https://tfhub.dev/google/tf2-preview/inception_v3/classification/4", output_shape=[1001])])
def fid_function(real_image_batch, generated_image_batch):
    """
    Given a batch of real images and a batch of generated images, this function pulls down a pre-trained inception 
    v3 network and then uses it to extract the activations for both the real and generated images. The distance of 
    these activations is then computed. The distance is a measure of how "realistic" the generated images are.

    :param real_image_batch: a batch of real images from the dataset, shape=[batch_size, height, width, channels]
    :param generated_image_batch: a batch of images generated by the generator network, shape=[batch_size, height, width, channels]

    :return: the inception distance between the real and generated images, scalar
    """
    INCEPTION_IMAGE_SIZE = (299, 299)
    real_resized = tf.image.resize(real_image_batch, INCEPTION_IMAGE_SIZE)
    fake_resized = tf.image.resize(generated_image_batch, INCEPTION_IMAGE_SIZE)
    module.build([None, 299, 299, 3])
    real_features = module(real_resized)
    fake_features = module(fake_resized)
    return tfgan.eval.frechet_classifier_distance_from_activations(real_features, fake_features)

class Generator_Model(tf.keras.Model):
    def __init__(self):
        """
        The model for the generator network is defined here. 
        """
        super(Generator_Model, self).__init__()
        # TODO: Define the model, loss, and optimizer
        self.model = tf.keras.Sequential()
        self.model.add(Dense(4*4*512, input_shape=(args.z_dim,), activation='relu'))
        self.model.add(BatchNormalization())
        self.model.add(Reshape((512, 4, 4)))

        self.model.add(Conv2DTranspose(filters=256, kernel_size=(5,5), strides=(2,2), padding='same', activation='relu'))
        self.model.add(BatchNormalization())

        self.model.add(Conv2DTranspose(filters=128, kernel_size=(5,5), strides=(2,2), padding='same', activation='relu'))
        self.model.add(BatchNormalization())
        
        self.model.add(Conv2DTranspose(filters=64, kernel_size=(5,5), strides=(2,2), padding='same', activation='relu'))
        self.model.add(BatchNormalization())

        self.model.add(Conv2DTranspose(filters=3, kernel_size=(5,5), strides=(2,2), padding='same', activation='tanh'))
        self.loss = tf.keras.losses.BinaryCrossentropy()
        self.optimizer = tf.keras.optimizers.Adam(learning_rate=0.0001)
        pass

    @tf.function
    def call(self, inputs):
        """
        Executes the generator model on the random noise vectors.

        :param inputs: a batch of random noise vectors, shape=[batch_size, z_dim]

        :return: prescaled generated images, shape=[batch_size, height, width, channel]
        """
        # TODO: Call the forward pass
        logits = self.model(inputs)
        return logits

    @tf.function
    def loss_function(self, disc_fake_output):
        """
        Outputs the loss given the discriminator output on the generated images.

        :param disc_fake_output: the discrimator output on the generated images, shape=[batch_size,1]

        :return: loss, the cross entropy loss, scalar
        """
        # TODO: Calculate the loss
        loss = self.loss(tf.ones_like(disc_fake_output), disc_fake_output)
        return loss

class Discriminator_Model(tf.keras.Model):
    def __init__(self):
        super(Discriminator_Model, self).__init__()
        """
        The model for the discriminator network is defined here. 
        """
        # TODO: Define the model, loss, and optimizer
        self.model = tf.keras.Sequential()
        self.model.add(Conv2D(filters=64, kernel_size=(5,5), strides=(2,2), padding='same', input_shape=(64, 64, 3)))
        self.model.add(LeakyReLU(alpha=0.2))


        self.model.add(Conv2D(filters=128, kernel_size=(5,5), strides=(2,2), padding='same'))
        self.model.add(BatchNormalization())
        self.model.add(LeakyReLU(alpha=0.2))


        self.model.add(Conv2D(filters=256, kernel_size=(5,5), strides=(2,2), padding='same'))
        self.model.add(BatchNormalization())
        self.model.add(LeakyReLU(alpha=0.2))

        
        self.model.add(Conv2D(filters=512, kernel_size=(5,5), strides=(2,2), padding='same'))
        self.model.add(BatchNormalization())
        self.model.add(LeakyReLU(alpha=0.2))


        self.model.add(Flatten())
        self.model.add(Dense(1))
        self.model.add(tf.keras.layers.Activation("sigmoid"))

        self.loss = tf.keras.losses.BinaryCrossentropy()
        self.optimizer = tf.keras.optimizers.Adam(learning_rate=0.0001)
        pass


    @tf.function
    def call(self, inputs):
        """
        Executes the discriminator model on a batch of input images and outputs whether it is real or fake.

        :param inputs: a batch of images, shape=[batch_size, height, width, channels]

        :return: a batch of values indicating whether the image is real or fake, shape=[batch_size, 1]
        """
        # TODO: Call the forward pass
        return self.model(inputs)

    def loss_function(self, disc_real_output, disc_fake_output):
        """
        Outputs the discriminator loss given the discriminator model output on the real and generated images.

        :param disc_real_output: discriminator output on the real images, shape=[batch_size, 1]
        :param disc_fake_output: discriminator output on the generated images, shape=[batch_size, 1]

        :return: loss, the combined cross entropy loss, scalar
        """
        # TODO: Calculate the loss
        D_loss = self.loss(tf.zeros_like(disc_fake_output), disc_fake_output)
        D_loss += self.loss(tf.ones_like(disc_real_output), disc_real_output)
        return D_loss

## --------------------------------------------------------------------------------------

# Train the model for one epoch.
def train(generator, discriminator, dataset_iterator, manager):
    """
    Train the model for one epoch. Save a checkpoint every 500 or so batches.

    :param generator: generator model
    :param discriminator: discriminator model
    :param dataset_ierator: iterator over dataset, see preprocess.py for more information
    :param manager: the manager that handles saving checkpoints by calling save()

    :return: The average FID score over the epoch
    """
    # Loop over our data until we run out
    for iteration, batch in enumerate(dataset_iterator):
        # TODO: Train the model
        z = tf.random.uniform([args.batch_size, args.z_dim],-1,1)
        with tf.GradientTape(persistent=True) as tape:
            gen_output = generator(z)
            logits_real = discriminator(batch)
            logits_fake = discriminator(gen_output)
            g_loss = generator.loss_function(logits_fake, logits_real)
            d_loss = discriminator.loss_function(logits_fake, logits_real)
        gradients = tape.gradient(g_loss, generator.trainable_variables)
        generator.optimizer.apply_gradients(zip(gradients, generator.trainable_variables))
        gradients = tape.gradient(d_loss, discriminator.trainable_variables)
        discriminator.optimizer.apply_gradients(zip(gradients, discriminator.trainable_variables))
        # Save
        if iteration % args.save_every == 0:
            manager.save()

        # Calculate inception distance and track the fid in order
        # to return the average
        totalfid, step = 0, 0
        if iteration % 500 == 0:
            fid_ = fid_function(batch, gen_output)
            print('**** INCEPTION DISTANCE: %g ****' % fid_)
            totalfid += fid_
            step += 1
    return totalfid/step


# Test the model by generating some samples.
def test(generator):
    """
    Test the model.

    :param generator: generator model

    :return: None
    """
    # TODO: Replace 'None' with code to sample a batch of random images
    img = None    

    ### Below, we've already provided code to save these generated images to files on disk
    # Rescale the image from (-1, 1) to (0, 255)
    img = ((img / 2) - 0.5) * 255
    # Convert to uint8
    img = img.astype(np.uint8)
    # Save images to disk
    for i in range(0, args.batch_size):
        img_i = img[i]
        s = args.out_dir+'/'+str(i)+'.png'
        imwrite(s, img_i)

## --------------------------------------------------------------------------------------

def main():
    # Load a batch of images (to feed to the discriminator)
    dataset_iterator = load_image_batch(args.img_dir, batch_size=args.batch_size, n_threads=args.num_data_threads)

    # Initialize generator and discriminator models
    generator = Generator_Model()
    discriminator = Discriminator_Model()

    # For saving/loading models
    checkpoint_dir = './checkpoints'
    checkpoint_prefix = os.path.join(checkpoint_dir, "ckpt")
    checkpoint = tf.train.Checkpoint(generator=generator, discriminator=discriminator)
    manager = tf.train.CheckpointManager(checkpoint, checkpoint_dir, max_to_keep=3)
    # Ensure the output directory exists
    if not os.path.exists(args.out_dir):
        os.makedirs(args.out_dir)

    if args.restore_checkpoint or args.mode == 'test':
        # restores the latest checkpoint using from the manager
        checkpoint.restore(manager.latest_checkpoint) 

    try:
        # Specify an invalid GPU device
        with tf.device('/device:' + args.device):
            if args.mode == 'train':
                for epoch in range(0, args.num_epochs):
                    print('========================== EPOCH %d  ==========================' % epoch)
                    avg_fid = train(generator, discriminator, dataset_iterator, manager)
                    print("Average FID for Epoch: " + str(avg_fid))
                    # Save at the end of the epoch, too
                    print("**** SAVING CHECKPOINT AT END OF EPOCH ****")
                    manager.save()
            if args.mode == 'test':
                test(generator)
    except RuntimeError as e:
        print(e)

if __name__ == '__main__':
   main()


