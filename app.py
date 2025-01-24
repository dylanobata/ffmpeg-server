from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import subprocess
import tempfile
import os
import json
from typing import List
import logging
from datetime import datetime
import shutil
import zipfile
from io import BytesIO
import re

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Your frontend URL
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class VideoProcessor:
    @staticmethod
    async def concatenate_videos(video1: UploadFile, video2: UploadFile) -> bytes:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Save uploaded files
            video1_path = os.path.join(temp_dir, "video1.mp4")
            video2_path = os.path.join(temp_dir, "video2.mp4")
            concat_list = os.path.join(temp_dir, "concat.txt")
            output_path = os.path.join(temp_dir, "output.mp4")
            
            # Write uploaded files to disk
            with open(video1_path, "wb") as f:
                f.write(await video1.read())
            with open(video2_path, "wb") as f:
                f.write(await video2.read())
            
            # Create concat file
            with open(concat_list, "w") as f:
                f.write(f"file '{video1_path}'\nfile '{video2_path}'")
            
            # Run FFmpeg command
            try:
                subprocess.run([
                    "ffmpeg", "-f", "concat", "-safe", "0",
                    "-i", concat_list, "-c", "copy", output_path
                ], check=True, capture_output=True)
                
                # Read the output file
                with open(output_path, "rb") as f:
                    return f.read()
            except subprocess.CalledProcessError as e:
                raise HTTPException(status_code=500, detail=f"FFmpeg error: {e.stderr.decode()}")

    @staticmethod
    async def standardize_video(video: UploadFile) -> bytes:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, "input.mp4")
            output_path = os.path.join(temp_dir, "output.mp4")
            
            # Write uploaded file to disk
            with open(input_path, "wb") as f:
                f.write(await video.read())
            
            try:
                subprocess.run([
                    "ffmpeg", "-i", input_path,
                    "-r", "30",
                    "-vf", "scale=-1080:1920",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "23",
                    output_path
                ], check=True, capture_output=True)
                
                with open(output_path, "rb") as f:
                    return f.read()
            except subprocess.CalledProcessError as e:
                raise HTTPException(status_code=500, detail=f"FFmpeg error: {e.stderr.decode()}")

    @staticmethod
    async def get_video_specs(video: UploadFile) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, "input.mp4")
            info_path = os.path.join(temp_dir, "info.json")
            
            # Write uploaded file to disk
            with open(input_path, "wb") as f:
                f.write(await video.read())
            
            try:
                subprocess.run([
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=codec_name,width,height,r_frame_rate,bit_rate:stream_tags=:format=bit_rate,format_name,size",
                    input_path,
                    "-o", info_path
                ], check=True, capture_output=True)
                
                with open(info_path, "r") as f:
                    info = json.load(f)
                
                video_stream = info.get("streams", [{}])[0]
                if not video_stream:
                    raise HTTPException(status_code=500, detail="No video stream found")
                
                # Calculate fps from frame rate fraction
                num, den = map(int, video_stream["r_frame_rate"].split("/"))
                fps = round(num / den)
                
                return {
                    "format": video_stream.get("format_name"),
                    "size": video_stream.get("size"),
                    "fps": fps,
                    "width": video_stream.get("width"),
                    "height": video_stream.get("height"),
                    "codec": video_stream.get("codec_name"),
                    "bitrate": video_stream.get("bit_rate")
                }
            except subprocess.CalledProcessError as e:
                raise HTTPException(status_code=500, detail=f"FFprobe error: {e.stderr.decode()}")

    @staticmethod
    async def combine_frames_to_video(frames: List[UploadFile], fps: int) -> bytes:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Save all frames
            for i, frame in enumerate(frames):
                frame_path = os.path.join(temp_dir, f"frame_{str(i).zfill(6)}.jpg")
                with open(frame_path, "wb") as f:
                    f.write(await frame.read())
            
            output_path = os.path.join(temp_dir, "output.mp4")
            
            try:
                subprocess.run([
                    "ffmpeg",
                    "-framerate", str(fps),
                    "-i", os.path.join(temp_dir, "frame_%06d.jpg"),
                    "-c:v", "libx264",
                    "-preset", "slow",
                    "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    "-tune", "film",
                    output_path
                ], check=True, capture_output=True)
                
                with open(output_path, "rb") as f:
                    return f.read()
            except subprocess.CalledProcessError as e:
                raise HTTPException(status_code=500, detail=f"FFmpeg error: {e.stderr.decode()}")

    @staticmethod
    async def process_video_with_overlays(video: UploadFile, filters: str, fps: int) -> bytes:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, "input.mp4")
            output_path = os.path.join(temp_dir, "output.mp4")
            
            # Write uploaded file to disk
            with open(input_path, "wb") as f:
                f.write(await video.read())
            
            try:
                subprocess.run([
                    "ffmpeg", "-i", input_path,
                    "-vf", filters,
                    "-r", str(fps),
                    "-c:v", "libx264",
                    "-preset", "fast",
                    output_path
                ], check=True, capture_output=True)
                
                with open(output_path, "rb") as f:
                    return f.read()
            except subprocess.CalledProcessError as e:
                raise HTTPException(status_code=500, detail=f"FFmpeg error: {e.stderr.decode()}")

    @staticmethod
    async def process_video(video: UploadFile, overlays: str, fps: int) -> bytes:
        overlays_data = json.loads(overlays)
        
        # Build FFmpeg filter complex for text overlays
        filter_complex = []
        for i, overlay in enumerate(overlays_data):
            # Convert color from hex to FFmpeg format if needed
            color = overlay['color'].lstrip('#')
            bg_color = overlay['backgroundColor'].lstrip('#')
            
            # Create drawtext filter for each overlay
            text_filter = (
                f"drawtext=text='{overlay['text']}':"
                f"fontsize={overlay['fontSize']}:"
                f"fontfile=/path/to/fonts/{overlay['fontFamily']}.ttf:"
                f"x={overlay['x']}:y={overlay['y']}:"
                f"fontcolor=0x{color}:"
                f"box=1:boxcolor=0x{bg_color}"  # Remove the @0.5 here since we handle it in the client
            )
            
            filter_complex.append(text_filter)
        
        # Combine all filters
        filters = ','.join(filter_complex)
        
        return await VideoProcessor.process_video_with_overlays(video, filters, fps)

    @staticmethod
    async def generate_thumbnails(video: UploadFile, num_thumbnails: int = 3) -> bytes:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, "input.mp4")
            thumbnails_dir = os.path.join(temp_dir, "thumbnails")
            os.makedirs(thumbnails_dir)
            
            # Write uploaded file to disk
            with open(input_path, "wb") as f:
                f.write(await video.read())
            
            try:
                # Get video duration
                duration_cmd = subprocess.run([
                    "ffprobe", 
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    input_path
                ], capture_output=True, text=True, check=True)
                duration = float(duration_cmd.stdout)
                
                # Calculate timestamp intervals
                interval = duration / (num_thumbnails + 1)
                timestamps = [interval * i for i in range(1, num_thumbnails + 1)]
                
                # Generate thumbnails
                for i, timestamp in enumerate(timestamps):
                    output_path = os.path.join(thumbnails_dir, f"thumb_{i}.jpg")
                    subprocess.run([
                        "ffmpeg",
                        "-ss", str(timestamp),
                        "-i", input_path,
                        "-vf", "scale=320:-1",  # 320px width, maintain aspect ratio
                        "-vframes", "1",
                        "-q:v", "2",  # High quality (2-31, lower is better)
                        output_path
                    ], check=True, capture_output=True)
                
                # Create zip file containing thumbnails
                zip_buffer = BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for filename in os.listdir(thumbnails_dir):
                        file_path = os.path.join(thumbnails_dir, filename)
                        zip_file.write(file_path, filename)
                
                return zip_buffer.getvalue()
                
            except subprocess.CalledProcessError as e:
                raise HTTPException(status_code=500, detail=f"FFmpeg error: {e.stderr.decode()}")

# API endpoints
@app.post("/concatenate")
async def concatenate_videos(video1: UploadFile, video2: UploadFile):
    result = await VideoProcessor.concatenate_videos(video1, video2)
    return Response(content=result, media_type="video/mp4")

@app.post("/standardize")
async def standardize_video(video: UploadFile):
    result = await VideoProcessor.standardize_video(video)
    return Response(content=result, media_type="video/mp4")

@app.post("/specs")
async def get_video_specs(video: UploadFile):
    return await VideoProcessor.get_video_specs(video)

@app.post("/combine-frames")
async def combine_frames(frames: List[UploadFile], fps: int):
    result = await VideoProcessor.combine_frames_to_video(frames, fps)
    return Response(content=result, media_type="video/mp4")

@app.post("/process-video")
async def process_video(video: UploadFile, overlays: str, fps: int):
    result = await VideoProcessor.process_video(video, overlays, fps)
    return Response(content=result, media_type="video/mp4")

@app.post("/generate-thumbnails")
async def generate_thumbnails(video: UploadFile, num_thumbnails: int = 3):
    result = await VideoProcessor.generate_thumbnails(video, num_thumbnails)
    return Response(content=result, media_type="application/zip")