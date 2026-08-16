from ultralytics import YOLO
import cv2
import os

# Load YOLO Model
print("Loading Weapon Detection Model...")
model = YOLO("models/weapon.pt")
print("Model Loaded Successfully!")

# Load Image
image_path = "test/image.jpg"
image = cv2.imread(image_path)
if image is None:
    print("Error: Image not found!")
    exit()
print("Image Loaded Successfully!")

# Perform Detection
results = model(image)
print(f"\nDetected {len(results)} result(s).\n")
# Draw Bounding Boxes
for result in results:
    boxes = result.boxes
    if len(boxes) == 0:
        print("No Weapon Detected.")
        continue
    for box in boxes:
        # Bounding box coordinates
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        # Confidence
        confidence = float(box.conf[0])
        # Since only one class exists
        label = f"Weapon {confidence:.2f}"
        # Draw rectangle
        cv2.rectangle(image, (x1, y1), (x2, y2),
                      (0, 0, 255), 2)
        # Draw label
        cv2.putText(image,
                    label,
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 0, 255),
                    2)
        print("Weapon Detected")
        print(f"Confidence : {confidence:.2f}")
# Save Output
os.makedirs("outputs", exist_ok=True)
output_path = "outputs/result.jpg"
cv2.imwrite(output_path, image)
print(f"\nResult saved to {output_path}")
# Show Result
cv2.imshow("Weapon Detection", image)
cv2.waitKey(0)
cv2.destroyAllWindows()
