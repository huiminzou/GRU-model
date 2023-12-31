import numpy as np
import torch
import tqdm
from sklearn.metrics import roc_auc_score, mean_squared_error, mean_absolute_error
import logging
import pandas as pd
from torch.utils.data import DataLoader

from torchfm.dataset.avazu import AvazuDataset
from torchfm.dataset.criteo import CriteoDataset
from torchfm.dataset.movielens import MovieLens1MDataset, MovieLens20MDataset
from torchfm.dataset.fux_tower import FuxTower
from torchfm.model.dnn import DeepNeuralNetwork
from torchfm.model.rnn import RecurrentNeuralNetwork
from torchfm.model.afi import AutomaticFeatureInteractionModel
from torchfm.model.afm import AttentionalFactorizationMachineModel
from torchfm.model.dcn import DeepCrossNetworkModel
from torchfm.model.dfm import DeepFactorizationMachineModel
from torchfm.model.ffm import FieldAwareFactorizationMachineModel
from torchfm.model.fm import FactorizationMachineModel
from torchfm.model.fnfm import FieldAwareNeuralFactorizationMachineModel
from torchfm.model.fnn import FactorizationSupportedNeuralNetworkModel
from torchfm.model.hofm import HighOrderFactorizationMachineModel
from torchfm.model.lr import LogisticRegressionModel
from torchfm.model.ncf import NeuralCollaborativeFiltering
from torchfm.model.nfm import NeuralFactorizationMachineModel
from torchfm.model.pnn import ProductNeuralNetworkModel
from torchfm.model.wd import WideAndDeepModel
from torchfm.model.xdfm import ExtremeDeepFactorizationMachineModel
from torchfm.model.afn import AdaptiveFactorizationNetwork
from sklearn.metrics import r2_score # R square
import time


def get_dataset(name, path):
    if name == 'movielens1M':
        return MovieLens1MDataset(path)
    elif name == 'movielens20M':
        return MovieLens20MDataset(path)
    elif name == 'criteo':
        return CriteoDataset(path)
    elif name == 'avazu':
        return AvazuDataset(path)
    elif name == 'fuxtower':
        return FuxTower(path)
    else:
        raise ValueError('unknown dataset name: ' + name)


def get_model(name, dataset):
    """
    Hyperparameters are empirically determined, not opitmized.
    """
    field_dims = dataset.field_dims
    # print(f'field_dims={field_dims}', field_dims.shape)
    # input()
    if name == 'dnn':
        return DeepNeuralNetwork(field_dims, embed_dim=16, mlp_dims=(512, 256, 128), dropout=0.2)
    elif name == 'rnn':
        return RecurrentNeuralNetwork(field_dims, feature_dims=feature_dim, hidden_size=256, num_layers=2, mlp_dims=(512, 256, 128), dropout=0.2)
    elif name == 'lr':
        return LogisticRegressionModel(field_dims)
    elif name == 'fm':
        return FactorizationMachineModel(field_dims, embed_dim=16)
    elif name == 'hofm':
        return HighOrderFactorizationMachineModel(field_dims, order=3, embed_dim=16)
    elif name == 'ffm':
        return FieldAwareFactorizationMachineModel(field_dims, embed_dim=4)
    elif name == 'fnn':
        return FactorizationSupportedNeuralNetworkModel(field_dims, embed_dim=16, mlp_dims=(16, 16), dropout=0.2)
    elif name == 'wd':
        return WideAndDeepModel(field_dims, embed_dim=16, mlp_dims=(16, 16), dropout=0.2)
    elif name == 'ipnn':
        return ProductNeuralNetworkModel(field_dims, embed_dim=16, mlp_dims=(16,), method='inner', dropout=0.2)
    elif name == 'opnn':
        return ProductNeuralNetworkModel(field_dims, embed_dim=16, mlp_dims=(16,), method='outer', dropout=0.2)
    elif name == 'dcn':
        return DeepCrossNetworkModel(field_dims, embed_dim=16, num_layers=3, mlp_dims=(16, 16), dropout=0.2)
    elif name == 'nfm':
        return NeuralFactorizationMachineModel(field_dims, embed_dim=64, mlp_dims=(64,), dropouts=(0.2, 0.2))
    elif name == 'ncf':
        # only supports MovieLens dataset because for other datasets user/item colums are indistinguishable
        assert isinstance(dataset, MovieLens20MDataset) or isinstance(dataset, MovieLens1MDataset)
        return NeuralCollaborativeFiltering(field_dims, embed_dim=16, mlp_dims=(16, 16), dropout=0.2,
                                            user_field_idx=dataset.user_field_idx,
                                            item_field_idx=dataset.item_field_idx)
    elif name == 'fnfm':
        return FieldAwareNeuralFactorizationMachineModel(field_dims, embed_dim=4, mlp_dims=(64,), dropouts=(0.2, 0.2))
    elif name == 'dfm':
        return DeepFactorizationMachineModel(field_dims, embed_dim=16, mlp_dims=(16, 16), dropout=0.2)
    elif name == 'xdfm':
        return ExtremeDeepFactorizationMachineModel(
            field_dims, embed_dim=16, cross_layer_sizes=(16, 16), split_half=False, mlp_dims=(16, 16), dropout=0.2)
    elif name == 'afm':
        return AttentionalFactorizationMachineModel(field_dims, embed_dim=16, attn_size=16, dropouts=(0.2, 0.2))
    elif name == 'afi':
        return AutomaticFeatureInteractionModel(
             field_dims, embed_dim=16, atten_embed_dim=64, num_heads=2, num_layers=3, mlp_dims=(400, 400), dropouts=(0, 0, 0))
    elif name == 'afn':
        print("Model:AFN")
        return AdaptiveFactorizationNetwork(
            field_dims, embed_dim=16, LNN_dim=1500, mlp_dims=(400, 400, 400), dropouts=(0, 0, 0))
    else:
        raise ValueError('unknown model name: ' + name)


class EarlyStopper(object):

    def __init__(self, num_trials, save_path):
        self.num_trials = num_trials
        self.trial_counter = 0
        self.best_rmse = 100
        self.save_path = save_path

    def is_continuable(self, model, rmse):
        if rmse < self.best_rmse:
            self.best_rmse = rmse
            self.trial_counter = 0
            torch.save(model, self.save_path)
            return True
        elif self.trial_counter + 1 < self.num_trials:
            self.trial_counter += 1
            return True
        else:
            return False


def train(model, optimizer, data_loader, criterion, device, log_interval=100):
    model.train()
    total_loss = 0
    targets, predicts = list(), list()
    tk0 = tqdm.tqdm(data_loader, smoothing=0, mininterval=1.0)
    for i, (fields, target) in enumerate(tk0):
        fields, target = fields.to(device), target.to(device)

        y = model(fields)
        print ('cccccccccccc',len(y))
        loss = criterion(y, target.float())
        #print ('train',y,target.float())
        model.zero_grad()
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
        if (i + 1) % log_interval == 0:
            tk0.set_postfix(loss=total_loss / log_interval)
            total_loss = 0
        targets.extend(target.tolist())
        predicts.extend(y.tolist())
    targets=np.hstack(np.array(targets))
    predicts=np.hstack(np.array(predicts))
    #print (targets,predicts)
    #print (len(targets),len(predicts))
    return mean_absolute_error(targets, predicts),r2_score(targets, predicts),mean_squared_error(targets, predicts),targets, predicts


def test(model, data_loader, device):
    model.eval()
    targets, predicts = list(), list()
    with torch.no_grad():
        for fields, target in tqdm.tqdm(data_loader, smoothing=0, mininterval=1.0):
            fields, target = fields.to(device), target.to(device)
            y = model(fields)
            #print ('test')
            targets.extend(target.tolist())
            predicts.extend(y.tolist())
            
    targets=np.hstack(np.array(targets))
    predicts=np.hstack(np.array(predicts))
    #print (len(targets),len(predicts))
    #print (targets,predicts)
    return mean_absolute_error(targets, predicts),r2_score(targets, predicts),mean_squared_error(targets, predicts), targets, predicts
    # return mean_squared_error(targets, predicts, squared=False), targets, predicts
    # return roc_auc_score(targets, predicts), targets, predicts


def main(dataset_name,
         dataset_path_train,
         dataset_path_test,
         model_name,
         epoch,
         learning_rate,
         batch_size,
         weight_decay,
         device,
         save_dir):
    device = torch.device(device)
    '''
    logging.getLogger().setLevel(logging.INFO)
    '''
    dataset_train = get_dataset(dataset_name, dataset_path_train)
    dataset_test = get_dataset(dataset_name, dataset_path_test)
    train_length = int(len(dataset_train) * 0.9)
    valid_length = len(dataset_train) - train_length
    # test_length = len(dataset_test)
    train_dataset, valid_dataset = torch.utils.data.random_split(
        dataset_train, (train_length, valid_length))
    test_dataset = dataset_test
    train_data_loader = DataLoader(train_dataset, batch_size=batch_size, num_workers=8)#,drop_last=True
    
    valid_data_loader = DataLoader(valid_dataset, batch_size=batch_size, num_workers=8)
    test_data_loader = DataLoader(test_dataset, batch_size=batch_size, num_workers=8)
        
    model = get_model(model_name, dataset_train).to(device)
    '''
    logging.info('# of params: {}'.format(sum(p.numel() for p in model.parameters())))
    logging.info('# of learnable params: {}'.format(sum(p.numel() for p in model.parameters() if p.requires_grad)))
    '''
    print("--------模型训练的参数量---------")
    print(sum(p.numel() for p in model.parameters() if p.requires_grad))  # 打印模型参数量
    criterion = torch.nn.SmoothL1Loss()  # BCELoss()
    optimizer = torch.optim.Adam(params=model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    #early_stopper = EarlyStopper(num_trials=10, save_path=f'{save_dir}/{model_name}.pt')
    save_path=f'{save_dir}/{model_name}.pt'
    
    mae=[]
    best=1000
    best_test=0
    best_mae=0
    for epoch_i in range(epoch):
        print (epoch_i)
        start_time = time.time()    

        mae_train0,r2_train0,mse_train0,targets_train0,predicts_train0 =train(model, optimizer, train_data_loader, criterion, device)
        end_time = time.time()
        total_time = end_time - start_time
        print ('total_time:',total_time)
        mae_train,r2_train,mse_train, targets_train, predicts_train = test(model, train_data_loader, device)
        
        #print ('---------------------------------------',mae_train0, r2_train0,mse_train0)
        #print ('---------------------------------------',mae_train, r2_train,mse_train)
        
        mae_val, r2_val,mse_val,targets_val, predicts_val = test(model, valid_data_loader, device)
        mae_test,r2_test,mse_test, targets_test, predicts_test= test(model, test_data_loader, device)
        #print('epoch:', epoch_i, 'validation: MAE:', mae_val)
        #print ('train_mae',mae_train)
        
        e=[]
        e.append(epoch_i)
        e.append(mae_train)
        e.append(mae_val)
        e.append(mae_test)
        
        e.append(r2_train)
        e.append(r2_val)
        e.append(r2_test)
        
        #print ('ccccccccccccccccccccccccccccc',mse_train)
        e.append(np.sqrt(mse_train))
        e.append(np.sqrt(mse_val))
        e.append(np.sqrt(mse_test))
        
        mae.append(e)

        if mae_val<best:
            best=mae_val
            best_test=predicts_test
            best_mae=mae_test
            best_train_mae=mae_train
            best_epoch=epoch_i
            
            print ('train_mae:',best_train_mae,'val_mae:',best,'test_mae:',best_mae)
            torch.save(model, save_path)
            #print(f'test MAE: {best_mae}')
            #print('r2_score:',r2_score(targets, predicts))
            
    df = pd.DataFrame(targets_test)
    df['prediction'] = pd.DataFrame(best_test)
    pd.DataFrame(df).to_csv('results_' + dataset_path_train[77:-10]+'_epoch=%s_lr=0.01_dropout=0.2_hidensize=256liu_GRU-5.csv'%(str(best_epoch)), index=None, header=['ground_truth', 'prediction'])#[67:-10]生长季，[51:-23]全年，[77:-10]非生长季
            
    mae=np.array(mae)
    df_mae=pd.DataFrame(mae)
    pd.DataFrame(df_mae).to_csv('mae_' + dataset_path_train[77:-10]+'_epoch=60_lr=0.01_dropout=0.2_hidensize=256liu_GRU-5.csv', index=None,header=['epoch','mae_train','mae_val','mae_test','r2_train','r2_val','r2_test','rmse_train','rmse_val','rmse_test'])


if __name__ == '__main__':
    import argparse

    #site_name=['CRP-Ref','AGR-C','AGR-Pr','AGR-Sw','CRP-C','CRP-Pr','CRP-Sw']
    site_name = ['CRP-Ref']
    for i in np.arange(len(site_name)):
        site = site_name[i]
        for j in np.arange(2018,2019,1):
            year = j
            print (site, year)
            #if (site == 'AGR-Sw' and year == 2019) or (site == 'AGR-Pr' and year == 2016) or (site == 'CRP-Ref' and year == 2011):#非生长季
            if (site == 'AGR-Pr' and (year == 2019 or year==2011)) or (site == 'CRP-C' and year == 2009):  # 生长季
                continue
            else:
                if (site == 'CRP-Ref' and year == 2009) or (site == 'CRP-C' and (year == 2011 or year == 2019)) or (site == 'CRP-Sw' and (year == 2012 or year == 2017 or year == 2018 or year == 2019)) or (site == 'AGR-C' and (year == 2009 or year == 2014 or year == 2019)) or (site == 'CRP-Pr' and (year == 2010 or year == 2011 or year == 2012 or year == 2013 or year == 2014 or year == 2019)):
                    feature_dim=26
                elif site == 'CRP-Ref' and (year == 2010 or year == 2011 or year == 2017):
                    feature_dim=30
                elif site == 'CRP-Ref' and year == 2014:
                    feature_dim=29
                elif site == 'CRP-Ref' and (year == 2012 or year == 2013 or year == 2015 or year == 2016 or year == 2018 or year == 2019 or year == 2020):
                    feature_dim=31
                elif (site == 'CRP-Sw' and (year == 2010 or year == 2011 or year == 2016 or year == 2017 or year == 2018 or year == 2019)) or (site=='AGR-C' and (year==2018 or year==2020)) or (site=='CRP-Pr' and (year==2018 or year==2017)) or (site=='AGR-Pr' and year==2017):
                    feature_dim=25
                else:
                    feature_dim = 27
                print (feature_dim)
                parser = argparse.ArgumentParser()
                parser.add_argument('--dataset_name', default='fuxtower')  # criteo
                parser.add_argument('--dataset_path_train', default='Data/Fux tower_all/Fux tower/filter_all_parameters/3-7_5days/seperate_season/'+site+'-'+str(year)+'-5-day_3-7_grow_train.csv')# , help='criteo/train.txt, avazu/train, or ml-1m/ratings.dat'
                parser.add_argument('--dataset_path_test', default='Data/Fux tower_all/Fux tower/filter_all_parameters/3-7_5days/seperate_season/'+site+'-'+str(year)+'-5-day_3-7_grow_test.csv')#'Data/Fux tower_all/Fux tower/filter_all_parameters/CRP-C-2009_after_filter_test.csv'
                parser.add_argument('--model_name', default='rnn', help='rnn, dnn')
                parser.add_argument('--epoch', type=int, default=60)
                
                parser.add_argument('--learning_rate', type=float, default=0.01)
                parser.add_argument('--batch_size', type=int, default=100)  #500, 2048
                parser.add_argument('--weight_decay', type=float, default=1e-6)
                parser.add_argument('--device', default='cuda:0')#'cuda:0')
                parser.add_argument('--save_dir', default='chkpt')
                args = parser.parse_args()
                main(args.dataset_name,
                     args.dataset_path_train,
                     args.dataset_path_test,
                     args.model_name,
                     args.epoch,
                     args.learning_rate,
                     args.batch_size,
                     args.weight_decay,
                     args.device,
                     args.save_dir)