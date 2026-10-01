"""SmallCNN (M1) architecture — vendored verbatim from train_image_pv.py:69-82.

The backend only needs the architecture class to load outputs_image/small_cnn/best.pt.
Importing the full trainer would pull in pandas/scikit-learn and its hardcoded D:/Potato
paths, none of which are needed at serving time. Parameter names (f, fc) must stay
identical so the checkpoint's state dict loads without key mismatches.
"""
import torch.nn as nn


class SmallCNN(nn.Module):
    def __init__(self, n=3, drop=0.4):
        super().__init__()
        def block(cin, cout):
            return nn.Sequential(nn.Conv2d(cin, cout, 3, padding=1, bias=False),
                nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
                nn.Conv2d(cout, cout, 3, padding=1, bias=False),
                nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
                nn.MaxPool2d(2), nn.Dropout2d(0.1))
        self.f = nn.Sequential(block(3, 32), block(32, 64), block(64, 128),
                               block(128, 256), nn.AdaptiveAvgPool2d(1))
        self.fc = nn.Sequential(nn.Flatten(), nn.Dropout(drop),
            nn.Linear(256, 128), nn.ReLU(inplace=True), nn.Dropout(drop), nn.Linear(128, n))
    def forward(self, x): return self.fc(self.f(x))
