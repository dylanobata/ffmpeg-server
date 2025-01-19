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
    async def combine_frames_to_video(zip_file: UploadFile, fps: int) -> bytes:
        logger.info(f"Starting combine_frames_to_video with zip file: {zip_file.filename}, fps: {fps}")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"Created temporary directory: {temp_dir}")
            
            # Read and extract zip file
            zip_content = await zip_file.read()
            with zipfile.ZipFile(BytesIO(zip_content)) as zip_ref:
                # Get list of image files and sort them
                image_files = [f for f in zip_ref.namelist() if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
                image_files.sort()  # Ensure frames are in order
                
                logger.info(f"Found {len(image_files)} image files in zip")
                
                # Extract all images
                zip_ref.extractall(temp_dir)
            
            output_path = os.path.join(temp_dir, "output.mp4")
            
            try:
                logger.info(f"Running FFmpeg command with fps={fps}")
                result = subprocess.run([
                    "ffmpeg",
                    "-framerate", str(fps),
                    "-pattern_type", "glob",
                    "-i", os.path.join(temp_dir, "*.jpg"),  # Adjust if you need to support other formats
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    output_path
                ], check=True, capture_output=True, text=True)
                
                logger.info("FFmpeg command completed successfully")
                
                with open(output_path, "rb") as f:
                    content = f.read()
                    logger.info(f"Output video size: {len(content)} bytes")
                    return content
            except subprocess.CalledProcessError as e:
                error_msg = f"FFmpeg error: {e.stderr}"
                logger.error(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                logger.error(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)

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
async def combine_frames(zip_file: UploadFile, fps: int):
    logger.info(f"Received combine-frames request: zip_file={zip_file.filename}, fps={fps}")
    
    # Input validation
    if not zip_file.filename.endswith('.zip'):
        logger.error("File must be a zip archive")
        raise HTTPException(status_code=400, detail="File must be a zip archive")
    
    if fps <= 0:
        logger.error(f"Invalid fps value: {fps}")
        raise HTTPException(status_code=400, detail="FPS must be greater than 0")
    
    try:
        result = await VideoProcessor.combine_frames_to_video(zip_file, fps)
        logger.info("Successfully combined frames into video")
        return Response(content=result, media_type="video/mp4")
    except Exception as e:
        logger.error(f"Error in combine_frames endpoint: {str(e)}")
        raise 