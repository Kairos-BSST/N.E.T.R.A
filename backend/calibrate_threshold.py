"""
calibrate_threshold.py
-----------------------
The shipped ANOMALY_THRESHOLD (0.01) in frame_processor.py was a hardcoded
guess in the original training script -- it was never checked against the
actual reconstruction-error distribution of normal footage, which is why
it fires on frames that look completely normal.

This script fixes that: point it at a video clip of NORMAL footage from
your actual camera/site (no weapons, no incidents, just everyday activity
-- the more representative of your real deployment, the better), and it
reports the mean/std/max reconstruction error plus a suggested threshold.

Usage:
    python calibrate_threshold.py path/to/normal_footage.mp4

Then set (env var, or edit ANOMALY_THRESHOLD default in frame_processor.py):
    ANOMALY_THRESHOLD=<suggested_threshold>
"""

import sys

import cv2
import numpy as np
import torch
import torch.nn as nn


class ConvolutionalAutoencoder(nn.Module):

    def __init__(self, input_channels=1, latent_dim=256):
        super().__init__()

        self.encoder = nn.Sequential(
            nn.Conv2d(input_channels, 32, 4, 2, 1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(32),

            nn.Conv2d(32, 64, 4, 2, 1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(64),

            nn.Conv2d(64, 128, 4, 2, 1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(128),

            nn.Conv2d(128, 256, 4, 2, 1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(256),
        )

        self.flatten = nn.Flatten()
        self.encode_fc = nn.Linear(4 * 4 * 256, 256)
        self.decode_fc = nn.Linear(256, 4 * 4 * 256)

        self.decoder = nn.Sequential(
            nn.ConvTranspose2d(256, 128, 4, 2, 1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(128),

            nn.ConvTranspose2d(128, 64, 4, 2, 1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(64),

            nn.ConvTranspose2d(64, 32, 4, 2, 1),
            nn.ReLU(inplace=True),
            nn.BatchNorm2d(32),

            nn.ConvTranspose2d(32, input_channels, 4, 2, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        x = self.encoder(x)
        b = x.size(0)
        x = self.flatten(x)
        x = self.encode_fc(x)
        x = self.decode_fc(x)
        x = x.view(b, 256, 4, 4)
        x = self.decoder(x)
        return x


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python calibrate_threshold.py path/to/normal_footage.mp4")
        sys.exit(1)

    video_path = sys.argv[1]
    device = torch.device("cpu")

    checkpoint = torch.load("models/anomaly.pth", map_location=device)
    model = ConvolutionalAutoencoder(
        checkpoint["model_info"]["input_channels"],
        checkpoint["model_info"]["latent_dimension"],
    ).to(device)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Cannot open video: {video_path}")
        sys.exit(1)

    errors = []

    with torch.no_grad():
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (64, 64))
            gray = gray.astype(np.float32) / 255.0

            tensor = torch.from_numpy(gray).unsqueeze(0).unsqueeze(0).to(device)
            reconstruction = model(tensor)
            error = torch.mean((tensor - reconstruction) ** 2).item()
            errors.append(error)

    cap.release()

    errors = np.array(errors)
    mean = errors.mean()
    std = errors.std()
    p95 = np.percentile(errors, 95)
    p99 = np.percentile(errors, 99)

    print(f"Frames analyzed:      {len(errors)}")
    print(f"Mean error:           {mean:.6f}")
    print(f"Std deviation:        {std:.6f}")
    print(f"Max error:            {errors.max():.6f}")
    print(f"95th percentile:      {p95:.6f}")
    print(f"99th percentile:      {p99:.6f}")
    print()
    print(f"Suggested threshold (mean + 3*std): {mean + 3 * std:.6f}")
    print(f"Suggested threshold (99th pct):     {p99:.6f}")
    print()
    print("Set ANOMALY_THRESHOLD env var to one of the suggestions above.")
    print("Start with mean + 3*std; if you still see false positives on")
    print("normal footage, use the 99th percentile value instead.")


if __name__ == "__main__":
    main()