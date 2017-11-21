from graphcnn.layers import *
from graphcnn.network_description import GraphCNNNetworkDescription
from tensorflow.python.layers import core as layers_core

class GraphCNNNetwork(object):
    def __init__(self):
        self.current_V = None
        self.current_A = None
        self.current_mask = None
        self.labels = None
        self.network_debug = False
        self.pred = None
                
    def create_network(self, input):
        self.current_V = input[0]
        self.current_A = input[1]
        self.labels = input[2]
        self.current_mask = input[3]
        self.mask = input[4]
        
        if self.network_debug:
            size = tf.reduce_sum(self.current_mask, axis=1)
            self.current_V = tf.Print(self.current_V, [tf.shape(self.current_V), tf.reduce_max(size), tf.reduce_mean(size)], message='Input V Shape, Max size, Avg. Size:')
        
        return input
        
        
    def make_batchnorm_layer(self):
        self.current_V = make_bn(self.current_V, self.is_training, mask=self.current_mask, num_updates = self.global_step)
        return self.current_V
        
    # Equivalent to 0-hop filter
    def make_embedding_layer(self, no_filters, name=None, with_bn=True, with_act_func=True):
        with tf.variable_scope(name, default_name='Embed') as scope:
            self.current_V = make_embedding_layer(self.current_V, no_filters)
            if with_bn:
                self.make_batchnorm_layer()
            if with_act_func:
                self.current_V = tf.nn.relu(self.current_V)
        return self.current_V, self.current_A, self.current_mask
        
    def make_dropout_layer(self, keep_prob=0.5):
        self.current_V = tf.cond(self.is_training, lambda:tf.nn.dropout(self.current_V, keep_prob=keep_prob), lambda:(self.current_V))
        return self.current_V
        
    def make_graphcnn_layer(self, no_filters, name=None, with_bn=True, with_act_func=True):
        with tf.variable_scope(name, default_name='Graph-CNN') as scope:
            self.current_V = make_graphcnn_layer(self.current_V, self.current_A, no_filters)
            if with_bn:
                self.make_batchnorm_layer()
            if with_act_func:
                self.current_V = tf.nn.relu(self.current_V)
            if self.network_debug:
                batch_mean, batch_var = tf.nn.moments(self.current_V, np.arange(len(self.current_V.get_shape())-1))
                self.current_V = tf.Print(self.current_V, [tf.shape(self.current_V), batch_mean, batch_var], message='"%s" V Shape, Mean, Var:' % scope.name)
        return self.current_V
        
    def make_graph_embed_pooling(self, no_vertices=1, name=None, with_bn=True, with_act_func=True):
        with tf.variable_scope(name, default_name='GraphEmbedPool') as scope:
            self.current_V, self.current_A = make_graph_embed_pooling(self.current_V, self.current_A, mask=self.current_mask, no_vertices=no_vertices)
            self.current_mask = None
            if with_bn:
                self.make_batchnorm_layer()
            if with_act_func:
                self.current_V = tf.nn.relu(self.current_V)
            if self.network_debug:
                batch_mean, batch_var = tf.nn.moments(self.current_V, np.arange(len(self.current_V.get_shape())-1))
                self.current_V = tf.Print(self.current_V, [tf.shape(self.current_V), batch_mean, batch_var], message='Pool "%s" V Shape, Mean, Var:' % scope.name)
        return self.current_V, self.current_A, self.current_mask
            
    def make_fc_layer(self, no_filters, name=None, with_bn=False, with_act_func=True):
        with tf.variable_scope(name, default_name='FC') as scope:
            self.current_mask = None
            
            if len(self.current_V.get_shape()) > 2:
                no_input_features = int(np.prod(self.current_V.get_shape()[1:]))
                self.current_V = tf.reshape(self.current_V, [-1, no_input_features])
            self.current_V = make_embedding_layer(self.current_V, no_filters)
            if with_bn:
                self.make_batchnorm_layer()
            if with_act_func:
                self.current_V = tf.nn.relu(self.current_V)
        return self.current_V
        
        
    def make_cnn_layer(self, no_filters, name=None, with_bn=False, with_act_func=True, filter_size=3, stride=1, padding='SAME'):
        with tf.variable_scope(None, default_name='conv') as scope:
            dim = self.current_V.get_shape()[-1]
            kernel = make_variable_with_weight_decay('weights',
                                                 shape=[filter_size, filter_size, dim, no_filters],
                                                 stddev=math.sqrt(1.0/(no_filters*filter_size*filter_size)),
                                                 wd=0.0005)
            conv = tf.nn.conv2d(self.current_V, kernel, [1, stride, stride, 1], padding=padding)
            biases = make_bias_variable('biases', [no_filters])
            self.current_V = tf.nn.bias_add(conv, biases)
            if with_bn:
                self.make_batchnorm_layer()
            if with_act_func:
                self.current_V = tf.nn.relu(self.current_V)
            return self.current_V
            
    def make_pool_layer(self, padding='SAME'):
        with tf.variable_scope(None, default_name='pool') as scope:
            dim = self.current_V.get_shape()[-1]
            self.current_V = tf.nn.max_pool(self.current_V, ksize=[1, 3, 3, 1], strides=[1, 2, 2, 1], padding=padding, name=scope.name)

            return self.current_V

    def make_rnn_layer(self,number_units,embedding_size,vocab_size):
        def process_decoder_input(target_data, batch_size):
            ending = tf.strided_slice(target_data, [0, 0], [batch_size, -1], [1, 1])
            dec_input = tf.concat([tf.fill([batch_size, 1], 1), ending], 1)
            return dec_input

        def make_cell(rnn_size,init_range=0.1):
            dec_cell = tf.contrib.rnn.LSTMCell(rnn_size,state_is_tuple=True,initializer=tf.random_uniform_initializer(-init_range, init_range, seed=2))
            return dec_cell


        with tf.variable_scope(None,default_name='decoder') as scope:
            batch_size = tf.shape(self.current_V)[0]
            dec_embeddings = tf.Variable(tf.random_uniform([vocab_size, embedding_size]))
            dec_cell = make_cell(number_units) #tf.contrib.rnn.MultiRNNCell([make_cell(number_units) for _ in range(1)])
            output_layer = layers_core.Dense(vocab_size, use_bias=False)

            test = tf.contrib.rnn.LSTMStateTuple(self.current_V,self.current_V)

            is_training = False
            def training_decoder(): 
                padded_size = tf.shape(self.mask)[1]
                sequence_length = tf.multiply(padded_size, tf.ones([batch_size],dtype=tf.int32))
                dec_input = process_decoder_input(self.labels,batch_size)
                dec_embed_input = tf.nn.embedding_lookup(dec_embeddings, dec_input)
                helper = tf.contrib.seq2seq.TrainingHelper(inputs=dec_embed_input,sequence_length=sequence_length,time_major=False)
                decoder = tf.contrib.seq2seq.BasicDecoder(cell=dec_cell,helper=helper,initial_state=test,output_layer=output_layer) 
                decder_output = tf.contrib.seq2seq.dynamic_decode(decoder,impute_finished=True,maximum_iterations=padded_size)[0] 
                return decder_output.rnn_output
            def testing_decoder(): 
                padded_size = 300
                start_tokens = tf.tile(tf.constant([1], dtype=tf.int32), [batch_size], name='start_tokens')
                end_token = tf.constant(2,shape=[1])
                # helper = tf.contrib.seq2seq.GreedyEmbeddingHelper(dec_embeddings,start_tokens,2)
                # decoder = tf.contrib.seq2seq.BasicDecoder(cell=dec_cell,helper=helper,initial_state=test,output_layer=output_layer) 
                decoder = tf.contrib.seq2seq.BeamSearchDecoder(dec_cell,dec_embeddings,start_tokens,2,test,16,output_layer,0.0)
                decder_output = tf.contrib.seq2seq.dynamic_decode(decoder,impute_finished=True,maximum_iterations=padded_size)[0] 
                self.pred = tf.identity(decder_output.sample_id, name='predictions')
                return training_decoder()


            self.current_V = tf.cond(self.is_training,training_decoder,testing_decoder)

            return self.current_V
