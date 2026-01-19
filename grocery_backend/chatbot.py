import os
import json
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from openai import OpenAI
import tempfile
from urllib.parse import quote
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

# =========================
# ENV SETUP
# =========================
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PRODUCT_API_URL = os.getenv("PRODUCT_API_URL", "http://0.0.0.0:8000/products")
BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://0.0.0.0:8000")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY missing")

# =========================
# FASTAPI APP
# =========================
app = FastAPI(title="E-commerce Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# OPENAI CLIENT
# =========================
client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# REQUEST MODELS
# =========================
class ChatRequest(BaseModel):
    query: str
    user_id: str
    access_token: str

class VoiceChatRequest(BaseModel):
    user_id: str
    access_token: str
    voice: str = "alloy"  # Options: alloy, echo, fable, onyx, nova, shimmer

# =========================
# LOAD PRODUCTS FROM YOUR API
# =========================
def fetch_products():
    response = requests.get(PRODUCT_API_URL, timeout=15)
    response.raise_for_status()
    return response.json()

# =========================
# BUILD VECTOR STORE
# =========================
def build_vector_store():
    products = fetch_products()
    documents = []

    for p in products:
        content = f"""
        Product ID: {p['id']}
        Product Name: {p['name']}
        Description: {p.get('description', '')}
        Brand: {p.get('brand', '')}
        Category: {p.get('category', '')}
        Price: {p['price']} €
        Stock: {p['stock']}
        """

        documents.append(
            Document(
                page_content=content.strip(),
                metadata={
                    "product_id": p["id"],
                    "product_name": p["name"],
                    "price": p["price"],
                    "stock": p["stock"]
                }
            )
        )

    embeddings = OpenAIEmbeddings()
    return FAISS.from_documents(documents, embeddings)

# Build index at startup
vector_store = build_vector_store()

# =========================
# FUNCTION IMPLEMENTATIONS
# =========================
def search_products(query: str) -> str:
    """Search for products based on user query."""
    docs = vector_store.similarity_search(query, k=4)
    
    if not docs:
        return "No products found matching your query."
    
    result = "Here are the products I found:\n\n"
    for doc in docs:
        result += f"- Product ID: {doc.metadata['product_id']}\n"
        result += f"  Name: {doc.metadata['product_name']}\n"
        result += f"  Price: {doc.metadata['price']} €\n"
        result += f"  Stock: {doc.metadata['stock']}\n\n"
    
    return result


def add_to_wishlist(product_id: str, user_id: str, access_token: str) -> str:
    """Add a product to the user's wishlist."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {"product_id": product_id}
        
        response = requests.post(
            f"{BACKEND_API_URL}/wishlist/{user_id}",
            json=data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            return f"✓ Successfully added product {product_id} to your wishlist!"
        elif response.status_code == 400:
            return "This product is already in your wishlist."
        else:
            return f"Failed to add to wishlist. Error: {response.text}"
    except Exception as e:
        return f"Error adding to wishlist: {str(e)}"


def add_to_cart(product_id: str, user_id: str, access_token: str, quantity: int = 1) -> str:
    """Add a product to the user's cart."""
    try:
        # First verify the product exists
        product = requests.get(f"{BACKEND_API_URL}/products/{product_id}", timeout=10)
        if product.status_code != 200:
            return f"Product {product_id} not found. Please search for products first."
        
        product_data = product.json()
        
        headers = {"Authorization": f"Bearer {access_token}"}
        data = {"product_id": product_id, "quantity": quantity}
        
        response = requests.post(
            f"{BACKEND_API_URL}/cart/{user_id}",
            json=data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            return f"✓ Successfully added {quantity} x {product_data['name']} (€{product_data['price']}) to your cart!"
        else:
            error_detail = response.json().get('detail', response.text)
            return f"Failed to add to cart: {error_detail}"
    except Exception as e:
        return f"Error adding to cart: {str(e)}"


def get_wishlist(user_id: str, access_token: str) -> str:
    """Get all items in the user's wishlist."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = requests.get(
            f"{BACKEND_API_URL}/wishlist/{user_id}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            items = response.json()
            if not items:
                return "Your wishlist is empty."
            
            result = "Your wishlist contains:\n"
            for item in items:
                result += f"- Product ID: {item['product_id']}\n"
            return result
        else:
            return f"Failed to get wishlist. Error: {response.text}"
    except Exception as e:
        return f"Error getting wishlist: {str(e)}"


def get_cart(user_id: str, access_token: str) -> str:
    """Get all items in the user's cart."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = requests.get(
            f"{BACKEND_API_URL}/cart/{user_id}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            items = response.json()
            if not items:
                return "Your cart is empty."
            
            result = "🛒 Your cart contains:\n\n"
            total = 0
            for item in items:
                # Get product details for each cart item
                product = requests.get(f"{BACKEND_API_URL}/products/{item['product_id']}", timeout=10)
                if product.status_code == 200:
                    product_data = product.json()
                    item_total = product_data['price'] * item['quantity']
                    total += item_total
                    result += f"- {product_data['name']}\n"
                    result += f"  Quantity: {item['quantity']}\n"
                    result += f"  Price: €{product_data['price']} each\n"
                    result += f"  Subtotal: €{item_total:.2f}\n\n"
                else:
                    result += f"- Product ID: {item['product_id']}, Quantity: {item['quantity']}\n\n"
            
            result += f"💰 Total: €{total:.2f}"
            return result
        else:
            return f"Failed to get cart. Error: {response.text}"
    except Exception as e:
        return f"Error getting cart: {str(e)}"


def remove_from_wishlist(product_id: str, user_id: str, access_token: str) -> str:
    """Remove a product from the user's wishlist."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = requests.delete(
            f"{BACKEND_API_URL}/wishlist/{user_id}/{product_id}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            return f"✓ Successfully removed product {product_id} from your wishlist!"
        else:
            return f"Failed to remove from wishlist. Error: {response.text}"
    except Exception as e:
        return f"Error removing from wishlist: {str(e)}"


def remove_from_cart(product_id: str, user_id: str, access_token: str) -> str:
    """Remove a product from the user's cart."""
    try:
        # Get product name first
        product = requests.get(f"{BACKEND_API_URL}/products/{product_id}", timeout=10)
        product_name = product.json()['name'] if product.status_code == 200 else product_id
        
        headers = {"Authorization": f"Bearer {access_token}"}
        
        response = requests.delete(
            f"{BACKEND_API_URL}/cart/{user_id}/{product_id}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            return f"✓ Successfully removed {product_name} from your cart!"
        else:
            error_detail = response.json().get('detail', response.text)
            return f"Failed to remove from cart: {error_detail}"
    except Exception as e:
        return f"Error removing from cart: {str(e)}"


def buy_products(user_id: str, access_token: str) -> str:
    """Purchase all products in the user's cart by creating an invoice."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # First, get the cart contents
        cart_response = requests.get(
            f"{BACKEND_API_URL}/cart/{user_id}",
            headers=headers,
            timeout=10
        )
        
        if cart_response.status_code != 200:
            return "Failed to retrieve your cart. Please try again."
        
        cart_items = cart_response.json()
        
        if not cart_items:
            return "Your cart is empty! Add some products to your cart first before purchasing."
        
        # Prepare invoice data
        products = []
        total = 0
        product_details = []
        
        for item in cart_items:
            # Get product details
            product_response = requests.get(
                f"{BACKEND_API_URL}/products/{item['product_id']}", 
                timeout=10
            )
            
            if product_response.status_code == 200:
                product_data = product_response.json()
                
                # Check stock availability
                if product_data['stock'] < item['quantity']:
                    return f"⚠️ Insufficient stock for {product_data['name']}. Available: {product_data['stock']}, Requested: {item['quantity']}"
                
                products.append({
                    "product_id": item['product_id'],
                    "quantity": item['quantity']
                })
                
                item_total = product_data['price'] * item['quantity']
                total += item_total
                product_details.append({
                    "name": product_data['name'],
                    "quantity": item['quantity'],
                    "price": product_data['price'],
                    "subtotal": item_total
                })
        
        # Create invoice
        invoice_data = {
            "user_id": user_id,
            "products": products
        }
        
        invoice_response = requests.post(
            f"{BACKEND_API_URL}/invoices",
            json=invoice_data,
            headers=headers,
            timeout=10
        )
        
        if invoice_response.status_code == 200:
            invoice = invoice_response.json()
            
            # Clear the cart after successful purchase
            for item in cart_items:
                requests.delete(
                    f"{BACKEND_API_URL}/cart/{user_id}/{item['product_id']}",
                    headers=headers,
                    timeout=10
                )
            
            # Format success message
            result = "🎉 Purchase successful! Here's your order summary:\n\n"
            result += f"Invoice ID: {invoice['id']}\n\n"
            result += "Items purchased:\n"
            
            for detail in product_details:
                result += f"- {detail['name']}\n"
                result += f"  Quantity: {detail['quantity']} x €{detail['price']}\n"
                result += f"  Subtotal: €{detail['subtotal']:.2f}\n\n"
            
            result += f"💰 Total Amount: €{invoice['total_amount']:.2f}\n"
            result += f"📅 Order Date: {invoice['created_at']}\n\n"
            result += "Thank you for your purchase! 🛍️"
            
            return result
        else:
            error_detail = invoice_response.json().get('detail', invoice_response.text)
            return f"Failed to complete purchase: {error_detail}"
            
    except Exception as e:
        return f"Error processing purchase: {str(e)}"


# =========================
# FUNCTION DEFINITIONS FOR OPENAI
# =========================
functions = [
    {
        "name": "search_products",
        "description": "Search for products based on user query. Use this when users ask about products, prices, or availability.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query for products"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "add_to_wishlist",
        "description": "Add a product to the user's wishlist. IMPORTANT: Always search for the product first using search_products, then use the 'Product ID' field from search results (NOT the product name). The product_id is a long string like '6968bf21f537f86c52bfed93'.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "The exact Product ID from search_products results. This is a MongoDB ObjectId (long alphanumeric string), NOT the product name."
                }
            },
            "required": ["product_id"]
        }
    },
    {
        "name": "add_to_cart",
        "description": "Add a product to the user's cart. IMPORTANT: Always search for the product first using search_products, then use the 'Product ID' field from search results (NOT the product name). The product_id is a long string like '6968bf21f537f86c52bfed93'.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "The exact Product ID from search_products results. This is a MongoDB ObjectId (long alphanumeric string), NOT the product name."
                },
                "quantity": {
                    "type": "integer",
                    "description": "The quantity to add (default: 1)",
                    "default": 1
                }
            },
            "required": ["product_id"]
        }
    },
    {
        "name": "get_wishlist",
        "description": "Get all items in the user's wishlist. Use when user asks about their wishlist or saved items.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_cart",
        "description": "Get all items in the user's cart. Use when user asks about their cart or shopping basket.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "remove_from_wishlist",
        "description": "Remove a product from the user's wishlist. Use when user wants to delete or remove an item from wishlist.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "The ID of the product to remove"
                }
            },
            "required": ["product_id"]
        }
    },
    {
        "name": "remove_from_cart",
        "description": "Remove a product from the user's cart. Use when user wants to delete or remove an item from cart.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "The ID of the product to remove"
                }
            },
            "required": ["product_id"]
        }
    },
    {
        "name": "buy_products",
        "description": "Purchase all products in the user's cart by creating an invoice. Use when user wants to buy, purchase, checkout, or complete their order. This will clear the cart after successful purchase.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
]

# =========================
# FUNCTION MAPPER
# =========================
function_map = {
    "search_products": search_products,
    "add_to_wishlist": add_to_wishlist,
    "add_to_cart": add_to_cart,
    "get_wishlist": get_wishlist,
    "get_cart": get_cart,
    "remove_from_wishlist": remove_from_wishlist,
    "remove_from_cart": remove_from_cart,
    "buy_products": buy_products
}

# =========================
# CHAT ENDPOINT
# =========================
@app.post("/chat")
def chat(request: ChatRequest):
    try:
        messages = [
            {
                "role": "system",
                "content": """You are a helpful e-commerce shopping assistant. You help users:
                - Find products they're looking for
                - Add products to their wishlist or cart
                - View their wishlist and cart
                - Remove items from wishlist or cart
                - Purchase products (checkout)
                
                IMPORTANT WORKFLOW for adding products to cart/wishlist:
                1. When user asks about a product or wants to add it, ALWAYS use search_products first
                2. Look at the search results and find the "Product ID" field
                3. Use that exact Product ID (the long string starting with numbers/letters) to add to cart or wishlist
                4. NEVER use the product name as the ID - always use the Product ID from search results
                
                Example:
                User: "Add Nutella to cart"
                1. Call search_products with query="Nutella"
                2. From results, extract the Product ID (e.g., "6968bf21f537f86c52bfed93")
                3. Call add_to_cart with product_id="6968bf21f537f86c52bfed93"
                
                Always be friendly and helpful. Confirm actions clearly.
                When processing purchases, provide a clear summary with invoice details."""
            },
            {
                "role": "user",
                "content": request.query
            }
        ]
        
        # Initial API call
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            functions=functions,
            function_call="auto"
        )
        
        message = response.choices[0].message
        
        # Handle function calls
        while message.function_call:
            function_name = message.function_call.name
            function_args = json.loads(message.function_call.arguments)
            
            # Add user_id and access_token for functions that need them
            if function_name in ["add_to_wishlist", "add_to_cart", "get_wishlist", 
                                "get_cart", "remove_from_wishlist", "remove_from_cart", "buy_products"]:
                function_args["user_id"] = request.user_id
                function_args["access_token"] = request.access_token
            
            # Call the function with error handling
            try:
                function_response = function_map[function_name](**function_args)
            except Exception as func_error:
                # If function fails, provide error message to GPT so it can respond appropriately
                function_response = f"Error executing function: {str(func_error)}"
            
            # Add function response to messages
            messages.append({
                "role": "assistant",
                "content": None,
                "function_call": {
                    "name": function_name,
                    "arguments": message.function_call.arguments
                }
            })
            messages.append({
                "role": "function",
                "name": function_name,
                "content": function_response
            })
            
            # Get next response
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                functions=functions,
                function_call="auto"
            )
            message = response.choices[0].message
        
        return {
            "answer": message.content,
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =========================
# REBUILD INDEX
# =========================
@app.post("/chat/reindex")
def reindex_products():
    global vector_store
    vector_store = build_vector_store()
    return {"status": "Product index rebuilt successfully"}

# =========================
# SPEECH TO TEXT ENDPOINT
# =========================
@app.post("/voice/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    """Transcribe audio file to text using OpenAI Whisper."""
    temp_file_path = None
    try:
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp_file:
            content = await audio.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        # Transcribe using Whisper
        with open(temp_file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        
        # Clean up temp file
        os.remove(temp_file_path)
        
        return {
            "text": transcript.text,
            "status": "success"
        }
    except Exception as e:
        # Clean up temp file if it exists
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except:
                pass
        raise HTTPException(status_code=500, detail=str(e))

# =========================
# TEXT TO SPEECH ENDPOINT
# =========================
@app.post("/voice/synthesize")
def synthesize_speech(text: str, voice: str = "alloy"):
    """Convert text to speech using OpenAI TTS.
    Voice options: alloy, echo, fable, onyx, nova, shimmer
    """
    try:
        response = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text
        )
        
        # Return audio as streaming response
        return Response(
            content=response.content,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "attachment; filename=speech.mp3"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# =========================
# COMBINED VOICE CHAT ENDPOINT
# =========================
@app.post("/voice/chat")
async def voice_chat(
    audio: UploadFile = File(...),
    user_id: str = Form(None),
    access_token: str = Form(None),
    voice: str = Form("alloy")
):
    """Complete voice chat flow: transcribe audio -> process with function calling -> synthesize response.
    
    Args:
        audio: Audio file (WebM, MP3, WAV, etc.)
        user_id: User ID for authentication
        access_token: JWT access token
        voice: TTS voice (alloy, echo, fable, onyx, nova, shimmer)
    
    Returns:
        Audio response with synthesized speech
    """
    temp_file_path = None
    try:
        # Step 1: Transcribe audio to text
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp_file:
            content = await audio.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        with open(temp_file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        
        os.remove(temp_file_path)
        user_query = transcript.text
        
        print(f"[VOICE] Transcribed: {user_query}")
        
        # Step 2: Process with chatbot (including function calling)
        messages = [
            {
                "role": "system",
                "content": """You are a helpful e-commerce shopping assistant. You help users:
                - Find products they're looking for
                - Add products to their wishlist or cart
                - View their wishlist and cart
                - Remove items from wishlist or cart
                - Purchase products (checkout)
                
                IMPORTANT WORKFLOW for adding products to cart/wishlist:
                1. When user asks about a product or wants to add it, ALWAYS use search_products first
                2. Look at the search results and find the "Product ID" field
                3. Use that exact Product ID (the long string starting with numbers/letters) to add to cart or wishlist
                4. NEVER use the product name as the ID - always use the Product ID from search results
                
                Example:
                User: "Add Nutella to cart"
                1. Call search_products with query="Nutella"
                2. From results, extract the Product ID (e.g., "6968bf21f537f86c52bfed93")
                3. Call add_to_cart with product_id="6968bf21f537f86c52bfed93"
                
                Always be friendly and helpful. Confirm actions clearly.
                Keep responses concise and natural for voice conversation."""
            },
            {
                "role": "user",
                "content": user_query
            }
        ]
        
        # Initial API call
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            functions=functions,
            function_call="auto"
        )
        
        message = response.choices[0].message
        
        # Handle function calls
        while message.function_call:
            function_name = message.function_call.name
            function_args = json.loads(message.function_call.arguments)
            
            print(f"[VOICE] Calling function: {function_name} with args: {function_args}")
            
            # Add user_id and access_token for functions that need them
            if function_name in ["add_to_wishlist", "add_to_cart", "get_wishlist", 
                                "get_cart", "remove_from_wishlist", "remove_from_cart", "buy_products"]:
                function_args["user_id"] = user_id
                function_args["access_token"] = access_token
            
            # Call the function with error handling
            try:
                function_response = function_map[function_name](**function_args)
                print(f"[VOICE] Function response: {function_response[:200]}")  # Log first 200 chars
                print(f"[VOICE] Function response length: {len(function_response)}")
            except Exception as func_error:
                # If function fails, provide error message to GPT so it can respond appropriately
                function_response = f"Error executing function: {str(func_error)}"
                print(f"[VOICE] Function error: {func_error}")
                print(f"[VOICE] Function response length: {len(function_response)}")
            
            # Add function response to messages
            messages.append({
                "role": "assistant",
                "content": None,
                "function_call": {
                    "name": function_name,
                    "arguments": message.function_call.arguments
                }
            })
            messages.append({
                "role": "function",
                "name": function_name,
                "content": function_response
            })
            
            # Get next response
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                functions=functions,
                function_call="auto"
            )
            message = response.choices[0].message
        
        answer_text = message.content
        
        print(f"[VOICE] Final response length: {len(answer_text) if answer_text else 0}")
        
        # Step 3: Convert response to speech
        # Limit text length for TTS (max 4096 characters)
        if len(answer_text) > 4000:
            answer_text = answer_text[:3997] + "..."
        
        print(f"[VOICE] Converting to speech...")
        speech_response = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=answer_text
        )
        
        print(f"[VOICE] Speech generated successfully")
        
        # URL encode the text to handle special characters like € in headers
        encoded_query = quote(user_query, safe='')
        encoded_answer = quote(answer_text, safe='')
        
        # Return audio response
        return Response(
            content=speech_response.content,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "attachment; filename=response.mp3",
                "X-Transcribed-Text": encoded_query,  # URL-encoded transcribed text
                "X-Response-Text": encoded_answer     # URL-encoded response text
            }
        )
        
    except Exception as e:
        # Clean up temp file if it exists
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except:
                pass
        print(f"[VOICE] ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# =========================
# HEALTH CHECK
# =========================
@app.get("/health")
def health_check():
    return {"status": "healthy"}

# =========================
# UVICORN ENTRYPOINT
# =========================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "chatbot:app",
        host="0.0.0.0",
        port=8001,
        reload=True
    )