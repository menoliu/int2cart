import yaml
import sys
import torch
from torch import nn
from torch.optim import Adam, SGD
import numpy as np
# torch.autograd.set_detect_anomaly(True)

from modelling.models.builder import BackboneBuilder
from modelling.utils import OnehotDigitizer
from sidechainnet import load
from modelling.train import Trainer
from modelling.utils.default_scalers import *
from modelling.utils.get_gpu import handle_gpu
from modelling.losses.drmsd import drmsd_loss

#
settings_path = 'configs/debug_building.yml'
debug_mode = True
if len(sys.argv) > 1:
    settings_path = sys.argv[-1]
    debug_mode = False
settings = yaml.safe_load(open(settings_path, "r"))

device = [torch.device(dev) for dev in handle_gpu(settings['general']['device'])]


# data
if debug_mode:
    data = load('debug', with_pytorch='dataloaders',
            scn_dir=settings['data']['scn_data_dir'],
            batch_size=settings['training']['batch_size'],
            complete_structures_only=True,
            dynamic_batching=False,
            use_default_train_sampler=True,
            id_filter=("1A8D_1_A", "1H8L_1_A", "1NQA_1_O", "1VAE_1_A", "1X5X_1_A"))
else:
    data = load(settings['data']['casp_version'],
                thinning=settings['data']['thinning'],
                with_pytorch='dataloaders',
                scn_dir=settings['data']['scn_data_dir'],
                batch_size=settings['training']['batch_size'],
                complete_structures_only=True,
                filter_by_resolution=settings['data'].get("filter_resolution", False))

train = data['train']
val = data[f'valid-{settings["data"]["validation_similarity_level"]}']
test = data['test']

val = data['train']
test = data['train']

# create model and load weights
model = BackboneBuilder(settings)
if 'pretrained_state' in settings['training']:
    model_state = torch.load(settings['training']['pretrained_state'])["model_state_dict"]
    model.load_predictor_weights(model_state)


# optimizer
optimizer = settings['training'].get('optimizer', "Adam")
if optimizer.lower() == 'adam':
    optimizer = Adam(model.parameters(),
                    lr=settings['training']['lr'],
                    weight_decay=settings['training']['weight_decay'])
elif optimizer.lower() == 'sgd':
    momentum = settings['training'].get('momentum', 0)
    nestrov = settings['training'].get('nestrov', False)
    optimizer = SGD(model.parameters(),
                    lr=settings['training']['lr'],
                    weight_decay=settings['training']['weight_decay'],
                    momentum=momentum,
                    nesterov=nestrov)




# loss
angle_digitizer = OnehotDigitizer((-179, 179, settings['bins']['angle_bin_count']), binary=False)

n_ca_blens_digitizer = OnehotDigitizer(settings['bins']['n-ca_bin'], binary=False)
ca_c_blens_digitizer = OnehotDigitizer(settings['bins']['ca-c_bin'], binary=False)
c_n_blens_digitizer = OnehotDigitizer(settings['bins']['c-n_bin'], binary=False)



# training
trainer = Trainer(
    model=model,
    loss_fn=drmsd_loss,
    optimizer=optimizer,
    device=device,
    yml_path=settings_path,
    output_path=settings['general']['output'],
    script_name=__file__,
    lr_scheduler=settings['training']['lr_scheduler'],
    bond_length_bin=settings['bins']['bond_length_bin'],
    backbone_angle_bin=settings['bins']['backbone_angle_bin'],
    bin_references={
        "angles": angle_digitizer.get_reference(),
        "n_ca_bond_length": n_ca_blens_digitizer.get_reference(),
        "ca_c_bond_length": ca_c_blens_digitizer.get_reference(),
        "c_n_bond_length": c_n_blens_digitizer.get_reference()
    },
    checkpoint_log=settings['checkpoint']['log'],
    checkpoint_val=settings['checkpoint']['val'],
    checkpoint_test=settings['checkpoint']['test'],
    checkpoint_model=settings['checkpoint']['model'],
    verbose=settings['checkpoint']['verbose'],
    preempt=settings['training']['preempt'],
    debug=debug_mode,
    mode="building",
    build_block_size=settings['training'].get('build_block_size', None)
)

trainer.print_layers()

best_results, succeeded = trainer.train(
    epochs=settings['training']['epochs'],
    train_dataloader=train,
    val_dataloader=val,
    test_dataloader=test
)

# Log hyperparameters and the best performance of this run
run_hparams = settings['training']
run_hparams.update(settings['model'])
for hp in list(run_hparams):
    if type(run_hparams[hp]) in [bool, float, int]:
        run_hparams['hparams/'+hp] = run_hparams[hp]
    del run_hparams[hp]

hparam_metrics = {}
if best_results is not None:
    hparam_metrics["metrics/loss"] = best_results["val/loss"]
    hparam_metrics["metrics/rmse_bb_angle"] = best_results["val/rmse_bb_angle"]
    hparam_metrics["metrics/rmse_sc_tor"] = best_results["val/rmse_sc_tor"]
    hparam_metrics["metrics/rmse_blens"] = best_results["val/rmse_blens"]
trainer.logger.log_hparams(run_hparams, hparam_metrics)

if succeeded:
    print('Done!')
else:
    print('Error during training!')