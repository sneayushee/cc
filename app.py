from flask import Flask, Response, request, jsonify, render_template
from azure.storage.blob import BlobServiceClient
from werkzeug.utils import secure_filename
from flask_cors import CORS
import pyodbc 
import os 
import openai
from dotenv import load_dotenv
from PIL import Image
import io
import logging
logging.basicConfig(level=logging.DEBUG)

load_dotenv()

app = Flask(__name__)
CORS(app)

# Azure Blob Storage configuration
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME")

blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
container_client = blob_service_client.get_container_client(AZURE_CONTAINER_NAME)

# Azure OpenAI Credentials
API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")

openai.api_key = API_KEY

# Database Connection
DB_SERVER = os.getenv("DB_SERVER")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

def get_db_connection():
    conn_str = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server={DB_SERVER};"
        f"Database={DB_NAME};"
        f"UID={DB_USER};"
        f"PWD={DB_PASSWORD};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str)

# Allowed file extensions
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/add_product', methods=['POST'])
def add_product():
    try:
        logging.debug("=== ADD PRODUCT REQUEST RECEIVED ===")
        logging.debug(f"Request data: {request.form}")
        logging.debug(f"Request files: {request.files}")
        logging.debug(f"Request headers: {request.headers}")
        
        name = request.form.get("name")
        price = request.form.get("price")
        image = request.files.get("image")

        logging.debug(f"Name: {name}, Price: {price}, Image: {image}")

        if not name or not price:
            logging.error("Missing name or price")
            return jsonify({"error": "Missing name or price"}), 400
        
        # Validate price is a valid decimal number
        try:
            price = float(price)
        except ValueError:
            logging.error("Invalid price format")
            return jsonify({"error": "Price must be a valid number"}), 400
            
        if not image:
            logging.error("No image uploaded")
            return jsonify({"error": "No image uploaded"}), 400

        if not allowed_file(image.filename):
            logging.error(f"Invalid file type: {image.filename}")
            return jsonify({"error": f"Invalid file type: {image.filename}"}), 400
        
        # Secure and generate a unique filename
        filename = secure_filename(image.filename)
        unique_filename = f"{os.urandom(8).hex()}_{filename}"
        logging.debug(f"Generated filename: {unique_filename}")
        
        blob_client = container_client.get_blob_client(unique_filename)

        # Upload image to Azure Blob Storage
        try:
            logging.debug("Attempting to upload image to blob storage")
            blob_client.upload_blob(image, overwrite=True)
            logging.debug("Image upload successful")
        except Exception as e:
            logging.error(f"Failed to upload image: {str(e)}")
            return jsonify({"error": f"Failed to upload image: {str(e)}"}), 500

        # Get the image URL
        image_url = f"https://{blob_service_client.account_name}.blob.core.windows.net/{AZURE_CONTAINER_NAME}/{unique_filename}"
        logging.debug(f"Image URL: {image_url}")

        # Save product info in database
        try:
            logging.debug("Attempting to save product to database")
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO Products (name, price, image_url) VALUES (?, ?, ?)", (name, price, image_url))
            conn.commit()
            logging.debug("Product saved to database successfully")
            conn.close()
        except Exception as e:
            logging.error(f"Failed to save product to database: {str(e)}")
            return jsonify({"error": f"Failed to save product to database: {str(e)}"}), 500

        logging.debug("=== ADD PRODUCT COMPLETED SUCCESSFULLY ===")
        return jsonify({"message": "Product added successfully", "image_url": image_url})
    
    except Exception as e:
        logging.error(f"An unexpected error occurred: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/list_products', methods=['GET'])
def list_products():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, image_url FROM Products")
        products = cursor.fetchall()
        conn.close()
        
        # Convert Decimal to float for JSON serialization
        products_list = [{
            'id': row[0],
            'name': row[1],
            'price': float(row[2]),  # Convert Decimal to float
            'blob_name': row[3].split('/')[-1]
        } for row in products]
        
        return jsonify({'products': products_list})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get_image/<blob_name>', methods=['GET'])
def get_image(blob_name):
    try:
        blob_client = container_client.get_blob_client(blob_name)
        image_data = blob_client.download_blob().readall()
        
        # Determine MIME type based on file extension
        file_extension = blob_name.split('.')[-1].lower()
        if file_extension == 'jpg' or file_extension == 'jpeg':
            mimetype = 'image/jpeg'
        elif file_extension == 'png':
            mimetype = 'image/png'
        elif file_extension == 'gif':
            mimetype = 'image/gif'
        else:
            return jsonify({"error": "Unsupported image type"}), 400
        
        return Response(image_data, mimetype=mimetype)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get-characters', methods=['POST'])
def get_characters():
    try:
        data = request.json
        manga_name = data.get("manga_name")

        prompt = f"List the top 5 most popular characters from the manga '{manga_name}' with a short description. Start directly from the first character and end on the fifth character, without extra text."

        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[{"role": "system", "content": "You are an anime and manga expert."},
                      {"role": "user", "content": prompt}]
        )

        return jsonify({"characters": response.choices[0].message['content']})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/delete_product/<int:product_id>', methods=['DELETE'])
def delete_product(product_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First get the product to find the image blob
        cursor.execute("SELECT image_url FROM Products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        
        if not product:
            conn.close()
            return jsonify({"error": "Product not found"}), 404
            
        # Extract blob name from the image URL
        blob_name = product[0].split('/')[-1]
        
        # Delete from database
        cursor.execute("DELETE FROM Products WHERE id = ?", (product_id,))
        conn.commit()
        conn.close()
        
        # Delete the image from Azure Blob Storage
        try:
            blob_client = container_client.get_blob_client(blob_name)
            blob_client.delete_blob()
        except Exception as e:
            # Log the error but don't fail the request if blob deletion fails
            logging.error(f"Failed to delete blob: {str(e)}")
        
        return jsonify({"success": True, "message": f"Product {product_id} deleted successfully"})
    except Exception as e:
        logging.error(f"Failed to delete product: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/')
def index():
    return render_template('index.html')

# Debug routes
@app.route('/debug', methods=['GET'])
def debug():
    debug_info = {
        "app_running": True,
        "endpoints": {
            "add_product": "/add_product [POST]",
            "list_products": "/list_products [GET]",
            "test_db": "/test_db [GET]",
            "test_blob": "/test_blob [GET]"
        }
    }
    return jsonify(debug_info)

@app.route('/test_db', methods=['GET'])
def test_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Test basic connection
        cursor.execute("SELECT 1")
        basic_test = cursor.fetchone()[0] == 1
        
        # Check if Products table exists
        cursor.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'Products'")
        table_exists = cursor.fetchone()[0] > 0
        
        # Get table schema if it exists
        schema = []
        if table_exists:
            cursor.execute("SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'Products'")
            columns = cursor.fetchall()
            schema = [{"column": col[0], "type": col[1]} for col in columns]
        
        # Get product count if table exists
        product_count = 0
        if table_exists:
            cursor.execute("SELECT COUNT(*) FROM Products")
            product_count = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({
            "connection": "successful",
            "basic_test": basic_test,
            "table_exists": table_exists,
            "schema": schema,
            "product_count": product_count,
            "connection_string_pattern": f"Driver={{ODBC Driver 18 for SQL Server}};Server={DB_SERVER};Database={DB_NAME};UID=***;PWD=***;"
        })
    except Exception as e:
        return jsonify({
            "connection": "failed",
            "error": str(e),
            "connection_string_pattern": f"Driver={{ODBC Driver 18 for SQL Server}};Server={DB_SERVER};Database={DB_NAME};UID=***;PWD=***;"
        }), 500

@app.route('/test_blob', methods=['GET'])
def test_blob():
    try:
        # Test blob storage connection
        containers = [container.name for container in blob_service_client.list_containers()]
        
        # Check if our container exists
        container_exists = AZURE_CONTAINER_NAME in containers
        
        # Get blob count if container exists
        blob_count = 0
        container_properties = {}
        blobs = []
        
        if container_exists:
            # Get container properties
            container_client = blob_service_client.get_container_client(AZURE_CONTAINER_NAME)
            container_properties = {
                "name": AZURE_CONTAINER_NAME,
                "public_access": container_client.get_container_properties().get("public_access", "none")
            }
            
            # List blobs (limited to 10)
            blob_list = container_client.list_blobs()
            blob_items = []
            count = 0
            for blob in blob_list:
                if count >= 10:
                    break
                blob_items.append({
                    "name": blob.name,
                    "size": blob.size,
                    "url": f"https://{blob_service_client.account_name}.blob.core.windows.net/{AZURE_CONTAINER_NAME}/{blob.name}"
                })
                count += 1
            
            blob_count = len(list(container_client.list_blobs()))
            blobs = blob_items
        
        return jsonify({
            "connection": "successful",
            "storage_account": blob_service_client.account_name,
            "containers": containers,
            "container_exists": container_exists,
            "container_properties": container_properties,
            "blob_count": blob_count,
            "sample_blobs": blobs
        })
    except Exception as e:
        return jsonify({
            "connection": "failed",
            "error": str(e)
        }), 500

@app.route('/test_add_product', methods=['GET'])
def test_add_product():
    try:
        # Create a test product without needing file upload
        test_name = "Test Product"
        test_price = 99.99
        test_image_url = "https://via.placeholder.com/150"
        
        # Save product info in database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO Products (name, price, image_url) VALUES (?, ?, ?)", 
                       (test_name, test_price, test_image_url))
        conn.commit()
        
        # Get the inserted ID
        cursor.execute("SELECT @@IDENTITY")
        product_id = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({
            "message": "Test product added successfully", 
            "product": {
                "id": product_id,
                "name": test_name,
                "price": test_price,
                "image_url": test_image_url
            }
        })
    except Exception as e:
        return jsonify({
            "error": f"Test product insertion failed: {str(e)}"
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
