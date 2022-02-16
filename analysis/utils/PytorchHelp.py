# torch imports
import torch
import torch.nn as nn
import torch.optim as optim
import torch.utils.data as data
import pytorch_lightning as pl
from pytorch_lightning.callbacks import Callback
from torchmetrics import Accuracy
import numpy as np

"""
# help class for torch data loading
class ClassifierDataset(data.Dataset):
    def __init__(self, epoch, steps_per_epoch, X_data, y_data):
        self.X_data = X_data
        self.y_data = y_data

        self.epoch = epoch
        self.iter = steps_per_epoch

    def __len__(self):
        #return len(self.X_data)
        return self.iter

    def __getitem__(self, idx):

        new_idx = idx + (self.iter*self.epoch)

         if new_idx >= len(self.X_data):
            new_idx = new_idx % len(self.X_data)

        return self.X_data[new_idx], self.y_data[new_idx]
"""


class ClassifierDataset(data.Dataset):
    def __init__(self, X_data, y_data):
        self.X_data = X_data
        self.y_data = y_data

    def __getitem__(self, index):
        return self.X_data[index], self.y_data[index]

    def __len__(self):
        return len(self.X_data)


# torch Multiclassifer
class MulticlassClassification(pl.core.lightning.LightningModule):  # nn.Module
    def __init__(
        self, num_feature, num_class, means, stds, dropout, class_weights, n_nodes
    ):
        super(MulticlassClassification, self).__init__()

        # custom normalisation layer
        self.norm = NormalizeInputs(means, stds)

        self.layer_1 = nn.Linear(num_feature, n_nodes // 2)
        self.layer_2 = nn.Linear(n_nodes // 2, n_nodes)
        self.layer_3 = nn.Linear(n_nodes, n_nodes)

        self.layer_out = nn.Linear(n_nodes, num_class)
        self.softmax = nn.Softmax(dim=1)  # log_
        self.loss = nn.CrossEntropyLoss(weight=class_weights, reduction="mean")

        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(p=dropout)
        self.batchnorm1 = nn.BatchNorm1d(n_nodes // 2)
        self.batchnorm2 = nn.BatchNorm1d(n_nodes)
        self.batchnorm3 = nn.BatchNorm1d(n_nodes)

        self.accuracy = Accuracy()

        # define global curves
        self.accuracy_stats = {"train": [], "val": []}
        self.loss_stats = {"train": [], "val": []}

    def forward(self, x):
        x = self.norm(x)
        x = self.layer_1(x)
        # x = self.batchnorm1(x)
        x = self.relu(x)

        x = self.layer_2(x)
        # x = self.batchnorm2(x)
        x = self.relu(x)
        x = self.dropout(x)

        x = self.layer_3(x)
        # x = self.batchnorm3(x)
        x = self.relu(x)
        x = self.dropout(x)

        x = self.layer_out(x)
        x = self.softmax(x)

        return x

    def validation_step(self, batch, batch_idx):

        x, y = batch
        logits = self(x)
        # loss = nn.functional.nll_loss()
        # loss = nn.CrossEntropyLoss()
        loss_step = self.loss(logits, y)
        preds = torch.argmax(logits, dim=1)

        acc_step = self.accuracy(preds, y.argmax(dim=1))

        # Calling self.log will surface up scalars for you in TensorBoard
        self.log("val_loss", loss_step, prog_bar=True)
        self.log("val_acc", acc_step, prog_bar=True)
        return {"val_loss": loss_step, "val_acc": acc_step}

    def test_step(self, batch, batch_idx):
        # Here we just reuse the validation_step for testing
        return self.validation_step(batch, batch_idx)

    def training_step(self, batch, batch_idx):
        x, y = batch[0].squeeze(0), batch[1].squeeze(0)
        logits = self(x)
        # loss = nn.functional.nll_loss()
        # loss = nn.CrossEntropyLoss()
        preds = torch.argmax(logits, dim=1)
        acc_step = self.accuracy(preds, y.argmax(dim=1))
        # maybe we do this and a softmax layer at the end
        # loss = F.nll_loss(logits, y)
        loss_step = self.loss(logits, y)
        # loss_step.backward() not necessary here, done during ? optimizer I guess
        self.log(
            "train_loss",
            loss_step,
            on_step=True,
            on_epoch=True,
            prog_bar=True,
            logger=True,
        )
        self.log(
            "train_acc",
            acc_step,
            on_step=True,
            on_epoch=True,
            prog_bar=True,
            logger=True,
        )

        """
        sample weights?
        size=len(preds)
        sample_weight = torch.empty(size).uniform_(0, 1)
        loss=loss_step * sample_weight
        loss.mean().backward()
        loss =(loss * sample_weight / sample_weight.sum()).sum()
        """

        # HAS to be called loss!!!
        return {"loss": loss_step, "acc": acc_step}

    def training_epoch_end(self, outputs):
        # aggregating information over complete training

        acc_mean = np.mean([out["acc"].item() for out in outputs])
        loss_mean = np.mean([out["loss"].item() for out in outputs])

        # save epoch wise metrics for later
        self.loss_stats["train"].append(loss_mean)
        self.accuracy_stats["train"].append(acc_mean)

        # Has to return NONE
        # return outputs

    def validation_epoch_end(self, outputs):
        # average over batches, and save extra computed values
        loss_mean = np.mean([out["val_loss"].item() for out in outputs])
        acc_mean = np.mean([out["val_acc"].item() for out in outputs])

        # save epoch wise metrics for later
        self.loss_stats["val"].append(loss_mean)
        self.accuracy_stats["val"].append(acc_mean)

        # loss_mean = outputs['val_loss'].mean().item()
        # outputs["val_loss"] = loss_mean
        return outputs

    def configure_optimizers(self):
        return optim.Adam(self.parameters(), lr=1e-3)

    def get_metrics(self):
        # don't show the version number, does not really seem to work
        # maybe do it in the progressbar
        items = super().get_metrics()
        items.pop("v_num", None)
        return items


class NormalizeInputs(nn.Module):
    def __init__(self, means, stds):
        super(NormalizeInputs, self).__init__()
        self.mean = torch.tensor(means)
        self.std = torch.tensor(stds)

    def forward(self, input):
        x = input - self.mean
        x = x / self.std
        return x


class EventBatchSampler(data.Sampler):
    def __init__(self, y_data, batch_size, n_processes, steps_per_epoch):
        self.y_data = y_data
        self.batch_size = batch_size
        self.n_processes = n_processes
        self.steps_per_epoch = steps_per_epoch

    def __len__(self):
        # return len(self.y_data)
        return (self.batch_size // self.n_processes) * self.n_processes

    def __iter__(self):
        sub_batch_size = self.batch_size // self.n_processes
        for i in range(self.steps_per_epoch):

            try:
                batch_counter
            except:
                batch_counter = [0, 0, 0]

            indices_for_batch = []
            for i in range(self.n_processes):

                batch_counter[i] += 1

                # get correct indices for process
                check = self.y_data[:, i] == 1
                # prohibit creating batch if sample space is over
                if batch_counter[i] * sub_batch_size > np.sum(check):
                    batch_counter[i] = 0

                indices = np.where(check)[0]

                # return random indices for each process in same amount
                # choice = np.random.choice(
                #    np.arange(0, len(indices), 1), size=sub_batch_size, replace=False
                # )
                # indices_for_batch.append(indices[choice])

                # append next sliced batch
                indices_for_batch.append(
                    indices[
                        sub_batch_size
                        * batch_counter[i] : sub_batch_size
                        * (batch_counter[i] + 1)
                    ]
                )

                # check[indices[choice]] all True

            # shuffle indices so network does not get all events from one category in a big chunk
            array = np.concatenate(indices_for_batch)
            np.random.shuffle(array)
            yield array


class MyPrintingCallback(Callback):
    def on_init_start(self, trainer):
        print("Starting to init trainer!")

    def on_init_end(self, trainer):
        print("trainer is init now")

    def on_train_end(self, trainer, pl_module):
        print("do something when training ends")
