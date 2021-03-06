#!/usr/bin/python3
    
import tensorflow as tf
import numpy as np
import glob, time, os
import tflib as lib

from network import Network
from data import Data
from config import directories
from utils import Utils

import tflib as lib
import tflib.ops.linear
import tflib.ops.conv2d
import tflib.ops.batchnorm
import tflib.ops.deconv2d
import tflib.save_images
import tflib.mnist
import tflib.plot

class Model():  #object created in train.py
    def __init__(self, config, paths, dataset, name='gan_compression', evaluate=False):

        # Build the computational graph

        print('Building computational graph ...')
        self.G_global_step = tf.Variable(0, trainable=False)  
        self.D_global_step = tf.Variable(0, trainable=False)
        '''
        G_global_setup and D_global_setup are objects of tf.Variable class which are initialized with a value of 0. From tf documentation :
        trainable: If True, the default, also adds the variable to the graph collection GraphKeys.TRAINABLE_VARIABLES. This collection is used as the default list of variables to use by the Optimizer
        classes.
        ''' 
        self.handle = tf.placeholder(tf.string, shape=[])
        '''
acts as a placeholder. While Variables are trained over time, placeholders are used for input data that doesn't change as your model trains (like input images, and class labels for those images).
the first argument specifies the input type and the 2nd argument specifies the shape. In a placeholder, no initial value needs to be specified. Placeholder simply allocates block of memory for future use. generally Placeholders are used for input data ( they are kind of variables which we use to feed our model), where as Variables are parameters such as weights that we train over time.
        '''
        self.training_phase = tf.placeholder(tf.bool)

        # >>> Data handling
        self.path_placeholder = tf.placeholder(paths.dtype, paths.shape)
        self.test_path_placeholder = tf.placeholder(paths.dtype)            

        self.semantic_map_path_placeholder = tf.placeholder(paths.dtype, paths.shape)
        self.test_semantic_map_path_placeholder = tf.placeholder(paths.dtype)  

        train_dataset = Data.load_dataset(self.path_placeholder,
                                          config.batch_size,
                                          augment=False,
                                          training_dataset=dataset,
                                          use_conditional_GAN=config.use_conditional_GAN,
                                          semantic_map_paths=self.semantic_map_path_placeholder)

        test_dataset = Data.load_dataset(self.test_path_placeholder,
                                         config.batch_size,
                                         augment=False,
                                         training_dataset=dataset,
                                         use_conditional_GAN=config.use_conditional_GAN,
                                         semantic_map_paths=self.test_semantic_map_path_placeholder,
                                         test=True)

        self.iterator = tf.data.Iterator.from_string_handle(self.handle,
                                                                    train_dataset.output_types,
                                                                    train_dataset.output_shapes)

        self.train_iterator = train_dataset.make_initializable_iterator()
        self.test_iterator = test_dataset.make_initializable_iterator()

        if config.use_conditional_GAN:
            self.example, self.semantic_map = self.iterator.get_next()
        else:
            print('Check how many times get_next is called * * * * * * : ')
            self.example = self.iterator.get_next()

        # Global generator: Encode -> quantize -> reconstruct
        # =======================================================================================================>>>
        with tf.variable_scope('generator'):
            
            self.feature_map = Network.encoder(self.example, config, self.training_phase, config.channel_bottleneck)
            #self.ab = tf.Print(self.feature_map,[tf.shape(self.feature_map),self.feature_map])
            #print('self.ab is and its shape :  ', self.ab, self.ab.shape, self.ab.get_shape())
            #print(' self.feature_map.get_shape() : ',self.feature_map.get_shape())
            self.w_hat = Network.quantizer(self.feature_map, config)
                
            if config.use_conditional_GAN:
                self.semantic_feature_map = Network.encoder(self.semantic_map, config, self.training_phase, 
                    config.channel_bottleneck, scope='semantic_map')
                self.w_hat_semantic = Network.quantizer(self.semantic_feature_map, config, scope='semantic_map')

                self.w_hat = tf.concat([self.w_hat, self.w_hat_semantic], axis=-1)

            if config.sample_noise is True:
                print('Sampling noise...')
                # noise_prior = tf.contrib.distributions.Uniform(-1., 1.)
                # self.noise_sample = noise_prior.sample([tf.shape(self.example)[0], config.noise_dim])
                noise_prior = tf.contrib.distributions.MultivariateNormalDiag(loc=tf.zeros([config.noise_dim]), scale_diag=tf.ones([config.noise_dim]))
                v = noise_prior.sample(tf.shape(self.example)[0])
                Gv = Network.dcgan_generator(v, config, self.training_phase, C=config.channel_bottleneck, upsample_dim=config.upsample_dim)
                print('self.w_hat.shape : ',self.w_hat.shape)
                print('Gv.shape : ',Gv.shape)
                self.z = tf.concat([self.w_hat, Gv], axis=-1)
                #print("@@@@@@@@@@@@@shape of z : ", self.z.get_shape(), self.z.shape)
            else:
                self.z = self.w_hat

            self.reconstruction = Network.decoder(self.z, config, self.training_phase, C=config.channel_bottleneck)

        print('Real image shape:', self.example.get_shape().as_list())
        print('Reconstruction shape:', self.reconstruction.get_shape().as_list())

        if evaluate:
            return

        # Pass generated, real images to discriminator
        # =======================================================================================================>>>

        if config.use_conditional_GAN:
            # Model conditional distribution
            self.example = tf.concat([self.example, self.semantic_map], axis=-1)
            self.reconstruction = tf.concat([self.reconstruction, self.semantic_map], axis=-1)

        if config.multiscale:
            print('example.get_shape : ',self.example.get_shape() )
            print('reconstruction.get_shape : ',self.reconstruction.get_shape() )
            print('In if config.multiscale is true')
            D_x, D_x2 = Network.capsule_discriminator(self.example, config, self.training_phase,use_sigmoid=config.use_vanilla_GAN, mode='real')
            print("&&&&&&&&&&&&&&&&&&&&&&&shape of D_x :", D_x.get_shape().as_list(), D_x.shape)
            D_Gz, D_Gz2 = Network.capsule_discriminator(self.reconstruction, config, self.training_phase,use_sigmoid=config.use_vanilla_GAN, mode='reconstructed', reuse=True)
            print("%%%%%%%%%%%%%%%%%% shape of D_Gz :", D_Gz.get_shape(),D_Gz.shape)
        else:
            D_x = Network.capsule_discriminator(self.example, config, self.training_phase, use_sigmoid=config.use_vanilla_GAN, mode= 'real')
            D_Gz = Network.capsule_discriminator(self.reconstruction, config, self.training_phase, use_sigmoid=config.use_vanilla_GAN, mode = 'reconstructed', reuse=True)
        ''' 
        # Loss terms 
        # =======================================================================================================>>>
        if config.use_vanilla_GAN is True: # This is false in config by default
            # Minimize JS divergence
            D_loss_real = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(logits=D_x,
                labels=tf.ones_like(D_x)))
            D_loss_gen = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(logits=D_Gz,
                labels=tf.zeros_like(D_Gz)))
            self.D_loss = D_loss_real + D_loss_gen
            # G_loss = max log D(G(z))
            self.G_loss = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(logits=D_Gz,
                labels=tf.ones_like(D_Gz)))
        else:
            # Minimize $\chi^2$ divergence
            self.D_loss = tf.reduce_mean(tf.square(D_x - 1.)) + tf.reduce_mean(tf.square(D_Gz))
            print("$$$$$$$$$$$$$$ self.D_loss : ", self.D_loss, " type : ", self.D_loss.get_shape())
            self.G_loss = tf.reduce_mean(tf.square(D_Gz - 1.))
            print("$$$$$$$$$$$$$$ self.G_loss : ", self.G_loss, " type : ", self.G_loss.get_shape())            

            if config.multiscale:
                self.D_loss += tf.reduce_mean(tf.square(D_x2 - 1.)) # + tf.reduce_mean(tf.square(D_x4 - 1.))
                self.D_loss += tf.reduce_mean(tf.square(D_Gz2)) # + tf.reduce_mean(tf.square(D_Gz4))

        distortion_penalty = config.lambda_X * tf.losses.mean_squared_error(self.example, self.reconstruction)
        self.G_loss += distortion_penalty

        if config.use_feature_matching_loss:  # feature extractor for generator
            D_x_layers, D_Gz_layers = [j for i in Dk_x for j in i], [j for i in Dk_Gz for j in i]
            feature_matching_loss = tf.reduce_sum([tf.reduce_mean(tf.abs(Dkx-Dkz)) for Dkx, Dkz in zip(D_x_layers, D_Gz_layers)])
            self.G_loss += config.feature_matching_weight * feature_matching_loss
        '''
        gen_params = lib.params_with_name('Generator')

        # Obtain parameters differently for disciminator (we used variable scope previously)
        # disc_params = lib.params_with_name('Discriminator')
        trainable_vars = tf.trainable_variables()
        disc_params = [var for var in trainable_vars if var.name.startswith("CapsDiscrim")]


        # Loss terms 
        # =======================================================================================================>>>
        self.G_loss = -tf.reduce_mean(D_Gz)
        self.D_loss = tf.reduce_mean(D_Gz) - tf.reduce_mean(D_x)
        alpha = tf.random_uniform(shape=[config.batch_size,1], minval=0.,maxval=1.)
        differences = self.reconstruction - self.example
        x, config, training, actv=tf.nn.leaky_relu, use_sigmoid=False, ksize=4, mode='real', reuse=False):  
        interpolates = real_data + (alpha*differences)

        print(interpolates.get_shape())
   
        gradients = tf.gradients(capsule_discriminator(interpolates,config, training, reuse=True, batchsize=1), [interpolates])[0]
        slopes = tf.sqrt(tf.reduce_sum(tf.square(gradients), reduction_indices=[1]))
        gradient_penalty = tf.reduce_mean((slopes-1.)**2)
        disc_cost += LAMBDA*gradient_penalty

        G_opt = tf.train.AdamOptimizer(learning_rate=1e-4,beta1=0.5,beta2=0.9).minimize(gen_cost,var_list=gen_params)
        D_opt = tf.train.AdamOptimizer(learning_rate=5e-6, beta1=0.5,beta2=0.9).minimize(disc_cost, var_list=disc_params)


    clip_disc_weights = None

        
        
        '''
        # Optimization
        # =======================================================================================================>>>
        G_opt = tf.train.AdamOptimizer(learning_rate=config.G_learning_rate, beta1=0.5)
        D_opt = tf.train.AdamOptimizer(learning_rate=config.D_learning_rate, beta1=0.5)

        theta_G = Utils.scope_variables('generator')
        theta_D = Utils.scope_variables('discriminator')
        #print('Generator parameters:', theta_G)
        print('Generator parameters : scope variables of generator')
        #print('Discriminator parameters:', theta_D)
        print('Generator parameters : scope variables of discriminator')
        G_update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS, scope='generator')
        D_update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS, scope='discriminator')

        # Execute the update_ops before performing the train_step
        with tf.control_dependencies(G_update_ops):
            self.G_opt_op = G_opt.minimize(self.G_loss, name='G_opt', global_step=self.G_global_step, var_list=theta_G)
        with tf.control_dependencies(D_update_ops):
            self.D_opt_op = D_opt.minimize(self.D_loss, name='D_opt', global_step=self.D_global_step, var_list=theta_D)

        G_ema = tf.train.ExponentialMovingAverage(decay=config.ema_decay, num_updates=self.G_global_step)
        G_maintain_averages_op = G_ema.apply(theta_G)
        D_ema = tf.train.ExponentialMovingAverage(decay=config.ema_decay, num_updates=self.D_global_step)
        D_maintain_averages_op = D_ema.apply(theta_D)

        with tf.control_dependencies(G_update_ops+[self.G_opt_op]):
            self.G_train_op = tf.group(G_maintain_averages_op)
        with tf.control_dependencies(D_update_ops+[self.D_opt_op]):
            self.D_train_op = tf.group(D_maintain_averages_op)

        # >>> Monitoring
        # tf.summary.scalar('learning_rate', learning_rate)
        tf.summary.scalar('generator_loss', self.G_loss)
        tf.summary.scalar('discriminator_loss', self.D_loss)
        tf.summary.scalar('distortion_penalty', distortion_penalty)
        if config.use_feature_matching_loss:
            tf.summary.scalar('feature_matching_loss', feature_matching_loss)
        tf.summary.scalar('G_global_step', self.G_global_step)
        tf.summary.scalar('D_global_step', self.D_global_step)
        tf.summary.image('real_images', self.example[:,:,:,:3], max_outputs=4)
        tf.summary.image('compressed_images', self.reconstruction[:,:,:,:3], max_outputs=4)
        if config.use_conditional_GAN:
            tf.summary.image('semantic_map', self.semantic_map, max_outputs=4)
        self.merge_op = tf.summary.merge_all()

        self.train_writer = tf.summary.FileWriter(
            os.path.join(directories.tensorboard, '{}_train_{}'.format(name, time.strftime('%d-%m_%I:%M'))), graph=tf.get_default_graph())
        self.test_writer = tf.summary.FileWriter(
            os.path.join(directories.tensorboard, '{}_test_{}'.format(name, time.strftime('%d-%m_%I:%M'))))
        '''
        ererer
