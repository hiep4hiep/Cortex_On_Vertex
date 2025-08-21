#!/usr/bin/env python3
"""
Vertex AI Chat Backend using Python Flask and Google Cloud SDK
"""

import os
import logging
from datetime import datetime
import asyncio
import vertexai
from vertexai import agent_engines
from vertexai.preview import reasoning_engines
from google.adk.sessions import VertexAiSessionService
from dotenv import load_dotenv

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google.cloud import aiplatform
from google.oauth2 import service_account
from google.auth import default
import vertexai
import csv
import tempfile
import asyncio
from werkzeug.utils import secure_filename



# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
load_dotenv()
# Flask app setup
app = Flask(__name__)
CORS(app)

# Init Vertex AI
vertexai.init(
    project=os.getenv("GOOGLE_CLOUD_PROJECT"),
    location=os.getenv("GOOGLE_CLOUD_LOCATION"),
)
session_service = VertexAiSessionService(project=os.getenv("GOOGLE_CLOUD_PROJECT"),location=os.getenv("GOOGLE_CLOUD_LOCATION"))
AGENT_ENGINE_ID = os.getenv("AGENT_ENGINE_ID")
agent_engine = agent_engines.get(AGENT_ENGINE_ID)


# Routes
@app.route('/')
def index():
    """Serve the main HTML page"""
    return send_from_directory('.', 'index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    """Main chat endpoint"""
    try:
        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        message = data.get('message', '').strip()
        if not message:
            return jsonify({"error": "Message is required"}), 400
        
        session_id = data.get('session_id')
        if not session_id:
            session = agent_engine.create_session(
                user_id="web_app"
            )
            session_id = session.get('id')
        logger.info(f"Received chat message: {message[:100]}...")
        
        # Query the Vertex AI reasoning engine
        for response in agent_engine.stream_query(
            message=message,
            user_id="web_app",
            session_id=session_id
        ):
            result = response
        logger.info(f"Response received: {result}")
        if result.get("content").get("parts")[0].get("text"):
            logger.info(f"Returning response...")
            return jsonify({
                "response": result.get("content").get("parts")[0].get("text"),
                "session_id": session_id,
                "timestamp": result["timestamp"]
            })
        else:
            logger.info(f"Returning error {result['error']}")
            return jsonify({
                "error": result["error"],
                "timestamp": result["timestamp"]
            }), 500
            
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}")
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }), 500




@app.route('/api/batch_chat', methods=['POST'])
def batch_chat():
    """Batch chat endpoint: upload CSV -> process each line concurrently -> return CSV"""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Empty filename"}), 400
        
        filename = secure_filename(file.filename)

        # Read uploaded CSV (assume first column = prompt)
        messages = []
        reader = csv.reader(file.stream.read().decode("utf-8").splitlines())
        for row in reader:
            if row:
                messages.append(row[0].strip())

        # Async processing
        async def process_message(msg, semaphore):
            async with semaphore:
                try:
                    session = agent_engine.create_session(user_id="batch_job")
                    session_id = session.get("id")

                    result_text = ""
                    for response in agent_engine.stream_query(
                        message=msg,
                        user_id="batch_job",
                        session_id=session_id
                    ):
                        result = response
                    result_text = result.get("content").get("parts")[0].get("text", "")
                    return {"input": msg, "output": result_text}
                except Exception as e:
                    return {"input": msg, "output": f"ERROR: {str(e)}"}

        async def run_batch():
            semaphore = asyncio.Semaphore(5)  # limit concurrency to 5
            tasks = [process_message(m, semaphore) for m in messages]
            return await asyncio.gather(*tasks)

        results = asyncio.run(run_batch())

        # Write results to a temporary CSV
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        with open(temp_file.name, "w", newline='', encoding="utf-8") as out_csv:
            writer = csv.writer(out_csv)
            writer.writerow(["Input", "Output"])
            for r in results:
                writer.writerow([r["input"], r["output"]])

        return send_from_directory(
            directory=os.path.dirname(temp_file.name),
            path=os.path.basename(temp_file.name),
            as_attachment=True,
            download_name=f"batch_results_{filename}"
        )

    except Exception as e:
        logger.error(f"Error in batch_chat endpoint: {e}")
        return jsonify({
            "error": f"Internal server error: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({
        "error": "Internal server error",
        "timestamp": datetime.now().isoformat()
    }), 500


if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("🚀 Starting Vertex AI Chat Server")
    logger.info(f"🤖 Reasoning Engine ID: {AGENT_ENGINE_ID}")
    logger.info(f"🌐 Server will run on: http://0.0.0.0:8000")
    logger.info("=" * 50)
    
    # Run the Flask app
    app.run(
        host='0.0.0.0',
        port=8000,
        debug=os.getenv('DEBUG', 'false').lower() == 'true'
    )