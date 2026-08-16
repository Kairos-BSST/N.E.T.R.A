from ultralytics import YOLO
from paddleocr import PaddleOCR
import cv2
import re
import os

# Load Models
model = YOLO("models/OCR.pt")
ocr = PaddleOCR(
    use_textline_orientation=True,
    lang="en"
)
print("YOLO Loaded Successfully!")
print("PaddleOCR Loaded Successfully!")

# Open Video
video_path = "test/car_video.mp4"
cap = cv2.VideoCapture(video_path)
if not cap.isOpened():
    print("❌ Could not open video.")
    exit()

# Video Writer
os.makedirs("outputs", exist_ok=True)
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)
out = cv2.VideoWriter(
    "outputs/result.mp4",
    cv2.VideoWriter_fourcc(*'mp4v'),
    fps,
    (width, height)
)
print("Processing video...")

# Process Frames
while True:
    ret, frame = cap.read()
    if not ret:
        break
    results = model.predict(
        frame,
        conf=0.5,
        verbose=False
    )
    boxes = results[0].boxes.xyxy.cpu().numpy()
    for box in boxes:
        x1, y1, x2, y2 = map(int, box)
        plate = frame[y1:y2, x1:x2]
        result = ocr.predict(plate)
        plate_number = ""
        if result:
            for res in result:
                texts = res.get("rec_texts", [])
                if texts:
                    plate_number = re.sub(
                        r'[^A-Za-z0-9]',
                        '',
                        texts[0]
                    ).upper()
        # Draw Bounding Box
        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            (0, 255, 0),
            2
        )
        # Draw Plate Number
        cv2.putText(
            frame,
            plate_number,
            (x1, y1 - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2
        )
    out.write(frame)
    cv2.imshow("ALPR", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break
cap.release()
out.release()
cv2.destroyAllWindows()
print("✅ Video processing completed.")
print("Saved at: outputs/result.mp4")
