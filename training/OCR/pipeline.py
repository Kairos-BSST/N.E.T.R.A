from ultralytics import YOLO
from paddleocr import PaddleOCR
import cv2
import os
import re

# Load YOLO Model
model = YOLO("models/OCR.pt")

# Load PaddleOCR
ocr = PaddleOCR(
    use_textline_orientation=True,
    lang="en"
)
print("YOLO Loaded Successfully!")
print("PaddleOCR Loaded Successfully!")

# Load Test Image
image = cv2.imread("test/car.jpg")
if image is None:
    print("❌ Image not found!")
    exit()
print("✅ Image Loaded Successfully!")
print("Image Shape:", image.shape)

# Detect License Plate
results = model.predict(
    image,
    conf=0.5,
    verbose=False
)
boxes = results[0].boxes.xyxy.cpu().numpy()
if len(boxes) == 0:
    print("❌ No license plate detected!")
    exit()
print(f"✅ {len(boxes)} License Plate(s) Detected")
# Create output folder if it doesn't exist
os.makedirs("outputs", exist_ok=True)

# Process Each Detected Plate
for i, box in enumerate(boxes):
    x1, y1, x2, y2 = map(int, box)
    # Crop license plate
    plate = image[y1:y2, x1:x2]
    # Save cropped image
    crop_path = f"outputs/plate_{i+1}.jpg"
    cv2.imwrite(crop_path, plate)
    print(f"\n✅ Cropped plate saved at: {crop_path}")

    # OCR
    print("\nRunning OCR...")
    result = ocr.predict(plate)
    print("\n RAW OCR OUTPUT")
    print(result)
    print("====================================")
