from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import StreamingResponse
from schemas import UserCreate, UserResponse, UserUpdate, ProductCreate, ProductResponse, InvoiceCreate, InvoiceResponse, WishlistItemCreate, WishlistItemResponse, CartItemCreate, CartItemResponse, LoginRequest
from auth import hash_password, verify_password, create_access_token
from fastapi.security import OAuth2PasswordBearer
from bson import ObjectId
from models import users_collection, invoices_collection, products_collection, wishlist_collection, cart_collection, api_sync_collection
from fastapi.encoders import jsonable_encoder
from datetime import datetime
from jose import JWTError, jwt
import os
import time
import random
from fastapi.openapi.models import OAuthFlows as OAuthFlowsModel
from fastapi.security import OAuth2
from fastapi.middleware.cors import CORSMiddleware
import requests
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
import io

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (for development)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# JWT Auth setup    
SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key_here")  # fallback key
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid authentication")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ===============================
# Helper Functions
# ===============================
def user_helper(user) -> dict:
    return {
        "id": str(user["_id"]),
        "name": user["name"],
        "username": user["username"],
        "email": user["email"],
        "usertype": user["usertype"],
        "phone": user.get("phone"),
        "address": user.get("address")
    }


def product_helper(product) -> dict:
    images = product.get("images", [])
    image_url = images[0] if images and len(images) > 0 else None
    return {
        "id": str(product["_id"]),
        "name": product["name"],
        "description": product.get("description"),
        "brand": product.get("brand"),
        "price": product["price"],
        "category": product.get("category"),
        "stock": product.get("stock"),
        "image_url": image_url,
        "barcode": product.get("barcode"),
        "source": product.get("source", "manual")
    }

def invoice_helper(invoice) -> dict:
    return {
        "id": str(invoice["_id"]),
        "user_id": invoice["user_id"],
        "products": invoice["products"],
        "total_amount": invoice.get("total_amount", 0.0),
        "created_at": invoice.get("created_at", datetime.utcnow().isoformat())
    }



def item_helper(item, item_type: str) -> dict:
    if item_type == "wishlist":
        return {
            "id": str(item["_id"]),
            "user_id": item["user_id"],
            "product_id": item["product_id"]
        }
    elif item_type == "cart":
        return {
            "id": str(item["_id"]),
            "user_id": item["user_id"],
            "product_id": item["product_id"],
            "quantity": item.get("quantity", 1)
        }
    else:
        raise ValueError("Unknown item type")


def calculate_total_amount(products: list) -> float:
    total = 0
    for item in products:
        total += item["price"] * item["quantity"]
    return total


# ===============================
# AUTH ROUTE
# ===============================
@app.post("/token")
def login(login_data: LoginRequest):
    print("Login attempt for user:", login_data.email)
    user = users_collection.find_one({"email": login_data.email})
    print(user)
    if not user or not verify_password(login_data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user["email"]})
    return {"access_token": token, "token_type": "bearer", "user": user_helper(user)}


# ===============================
# USER ROUTES
# ===============================
@app.post("/users", response_model=UserResponse)
def create_user(user: UserCreate):
    if users_collection.find_one({"username": user.username}):
        raise HTTPException(status_code=400, detail="Username already exists")

    if users_collection.find_one({"email": user.email}):
        raise HTTPException(status_code=400, detail="Email already exists")

    hashed_pwd = hash_password(user.password)

    user_dict = user.dict()
    user_dict["password"] = hashed_pwd
    user_dict["created_at"] = datetime.utcnow().isoformat()

    result = users_collection.insert_one(user_dict)
    user_dict["_id"] = result.inserted_id

    return user_helper(user_dict)



@app.get("/users", response_model=list[UserResponse])
def get_users(current_user: str = Depends(get_current_user)):
    users = users_collection.find()
    return [user_helper(u) for u in users]


@app.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: str, current_user: str = Depends(get_current_user)):
    user = users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user_helper(user)


@app.put("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: str, user: UserUpdate, current_user: str = Depends(get_current_user)):
    user_dict = {k: v for k, v in user.dict().items() if v is not None}
    if "password" in user_dict:
        user_dict["password"] = hash_password(user_dict["password"])
    result = users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": user_dict})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    updated_user = users_collection.find_one({"_id": ObjectId(user_id)})
    return user_helper(updated_user)


@app.delete("/users/{user_id}")
def delete_user(user_id: str, current_user: str = Depends(get_current_user)):
    result = users_collection.delete_one({"_id": ObjectId(user_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted successfully"}


# ===============================
# PRODUCT ROUTES
# ===============================
@app.post("/products", response_model=ProductResponse)
def create_product(product: ProductCreate, current_user: str = Depends(get_current_user)):
    product_dict = jsonable_encoder(product)
    result = products_collection.insert_one(product_dict)
    product_dict["_id"] = result.inserted_id
    return product_helper(product_dict)


@app.get("/products", response_model=list[ProductResponse])
def get_all_products():
    products = products_collection.find()
    return [product_helper(p) for p in products]


@app.get("/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: str):
    product = products_collection.find_one({"_id": ObjectId(product_id)})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product_helper(product)


@app.put("/products/{product_id}", response_model=ProductResponse)
def update_product(product_id: str, product: ProductCreate, current_user: str = Depends(get_current_user)):
    update_data = {k: v for k, v in product.dict().items() if v is not None}
    updated = products_collection.update_one({"_id": ObjectId(product_id)}, {"$set": update_data})
    if updated.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    updated_product = products_collection.find_one({"_id": ObjectId(product_id)})
    return product_helper(updated_product)


@app.delete("/products/{product_id}")
def delete_product(product_id: str, current_user: str = Depends(get_current_user)):
    deleted = products_collection.delete_one({"_id": ObjectId(product_id)})
    if deleted.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"message": "Product deleted successfully"}


# ===============================
# INVOICE ROUTES
# ===============================
@app.post("/invoices", response_model=InvoiceResponse)
def create_invoice(invoice: InvoiceCreate, current_user: str = Depends(get_current_user)):
    populated_products = []
    for item in invoice.products:
        product = products_collection.find_one({"_id": ObjectId(item.product_id)})
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {item.product_id} not found")
        populated_products.append({
            "product_id": item.product_id,
            "product_name": product["name"],
            "price": product["price"],
            "quantity": item.quantity
        })
    total_amount = calculate_total_amount(populated_products)
    created_at = datetime.utcnow().isoformat()
    invoice_data = {
        "user_id": invoice.user_id,
        "products": populated_products,
        "total_amount": total_amount,
        "created_at": created_at
    }
    result = invoices_collection.insert_one(invoice_data)
    invoice_data["id"] = str(result.inserted_id)
    return invoice_data


@app.get("/invoices", response_model=list[InvoiceResponse])
def get_invoices(current_user: str = Depends(get_current_user)):
    # Get user to find their ID
    user = users_collection.find_one({"email": current_user})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user_id = str(user["_id"])
    invoices = invoices_collection.find({"user_id": user_id})
    return [invoice_helper(i) for i in invoices]



@app.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: str, current_user: str = Depends(get_current_user)):
    invoice = invoices_collection.find_one({"_id": ObjectId(invoice_id)})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice_helper(invoice)


@app.delete("/invoices/{invoice_id}")
def delete_invoice(invoice_id: str, current_user: str = Depends(get_current_user)):
    deleted = invoices_collection.delete_one({"_id": ObjectId(invoice_id)})
    if deleted.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"message": "Invoice deleted successfully"}


@app.get("/invoices/{invoice_id}/pdf")
def generate_invoice_pdf(invoice_id: str, current_user: str = Depends(get_current_user)):
    """Generate a PDF for a specific invoice"""
    # Fetch invoice
    invoice = invoices_collection.find_one({"_id": ObjectId(invoice_id)})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Fetch user details
    user = users_collection.find_one({"_id": ObjectId(invoice["user_id"])})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Create PDF in memory
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=18)
    
    # Container for the 'Flowable' objects
    elements = []
    
    # Define styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#10b981'),
        spaceAfter=30,
        alignment=TA_CENTER
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#374151'),
        spaceAfter=12,
    )
    
    # Add title
    title = Paragraph("INVOICE", title_style)
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    # Invoice Info
    invoice_info = [
        ['Invoice ID:', invoice_id[-8:].upper()],
        ['Date:', datetime.fromisoformat(invoice["created_at"]).strftime('%B %d, %Y %I:%M %p')],
        ['Customer:', user.get('name', 'N/A')],
        ['Email:', user.get('email', 'N/A')],
    ]
    
    info_table = Table(invoice_info, colWidths=[2*inch, 4*inch])
    info_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    
    elements.append(info_table)
    elements.append(Spacer(1, 20))
    
    # Add items header
    items_heading = Paragraph("Order Items", heading_style)
    elements.append(items_heading)
    elements.append(Spacer(1, 12))
    
    # Items table
    items_data = [['#', 'Product Name', 'Quantity', 'Price', 'Subtotal']]
    
    for idx, item in enumerate(invoice["products"], 1):
        product_name = item.get("product_name", "Unknown Product")
        quantity = item.get("quantity", 0)
        price = item.get("price", 0.0)
        subtotal = quantity * price
        
        items_data.append([
            str(idx),
            product_name,
            str(quantity),
            f"€{price:.2f}",
            f"€{subtotal:.2f}"
        ])
    
    # Add totals
    subtotal = invoice["total_amount"] * 0.9
    tax = invoice["total_amount"] * 0.1
    
    items_data.append(['', '', '', 'Subtotal:', f"€{subtotal:.2f}"])
    items_data.append(['', '', '', 'Tax (10%):', f"€{tax:.2f}"])
    items_data.append(['', '', '', 'Total:', f"€{invoice['total_amount']:.2f}"])
    
    items_table = Table(items_data, colWidths=[0.5*inch, 3*inch, 1*inch, 1*inch, 1.2*inch])
    items_table.setStyle(TableStyle([
        # Header row
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        
        # Data rows
        ('FONTNAME', (0, 1), (-1, -4), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -4), 10),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -4), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -4), [colors.white, colors.HexColor('#f9fafb')]),
        
        # Summary rows
        ('FONTNAME', (3, -3), (-1, -1), 'Helvetica-Bold'),
        ('ALIGN', (3, -3), (-1, -1), 'RIGHT'),
        ('LINEABOVE', (3, -3), (-1, -3), 1, colors.grey),
        ('LINEABOVE', (3, -1), (-1, -1), 2, colors.black),
        ('BACKGROUND', (3, -1), (-1, -1), colors.HexColor('#f3f4f6')),
        ('FONTSIZE', (3, -1), (-1, -1), 12),
    ]))
    
    elements.append(items_table)
    elements.append(Spacer(1, 30))
    
    # Footer
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.grey,
        alignment=TA_CENTER
    )
    footer = Paragraph("Thank you for your purchase! | GroceryStore", footer_style)
    elements.append(footer)
    
    # Build PDF
    doc.build(elements)
    
    # Get PDF data
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=invoice_{invoice_id[-8:].upper()}.pdf"
        }
    )


# ===============================
# WISHLIST ROUTES
# ===============================
@app.post("/wishlist/{user_id}", response_model=WishlistItemResponse)
def add_to_wishlist(user_id: str, item: WishlistItemCreate, current_user: str = Depends(get_current_user)):
    product = products_collection.find_one({"_id": ObjectId(item.product_id)})
    if not product:
        raise HTTPException(status_code=404, detail="Product does not exist")
    existing = wishlist_collection.find_one({"user_id": user_id, "product_id": item.product_id})
    if existing:
        raise HTTPException(status_code=400, detail="Product already in wishlist")
    item_dict = item.dict()
    item_dict["user_id"] = user_id
    result = wishlist_collection.insert_one(item_dict)
    item_dict["_id"] = result.inserted_id
    return item_helper(item_dict, "wishlist")


@app.get("/wishlist/{user_id}", response_model=list[WishlistItemResponse])
def get_wishlist(user_id: str, current_user: str = Depends(get_current_user)):
    items = wishlist_collection.find({"user_id": user_id})
    return [item_helper(i, "wishlist") for i in items]


@app.delete("/wishlist/{user_id}/{product_id}")
def delete_wishlist_item(user_id: str, product_id: str, current_user: str = Depends(get_current_user)):
    result = wishlist_collection.delete_one({"user_id": user_id, "product_id": product_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Item not found in wishlist")
    return {"message": "Item removed from wishlist"}


# ===============================
# CART ROUTES
# ===============================
@app.post("/cart/{user_id}", response_model=CartItemResponse)
def add_to_cart(user_id: str, item: CartItemCreate, current_user: str = Depends(get_current_user)):
    existing = cart_collection.find_one({"user_id": user_id, "product_id": item.product_id})
    if existing:
        cart_collection.update_one({"_id": existing["_id"]}, {"$inc": {"quantity": item.quantity}})
        existing_item = cart_collection.find_one({"_id": existing["_id"]})
        return item_helper(existing_item, "cart")
    item_dict = item.dict()
    item_dict["user_id"] = user_id
    result = cart_collection.insert_one(item_dict)
    item_dict["_id"] = result.inserted_id
    return item_helper(item_dict, "cart")


@app.get("/cart/{user_id}", response_model=list[CartItemResponse])
def get_cart(user_id: str, current_user: str = Depends(get_current_user)):
    items = cart_collection.find({"user_id": user_id})
    return [item_helper(i, "cart") for i in items]


@app.delete("/cart/{user_id}/{product_id}")
def delete_cart_item(user_id: str, product_id: str, current_user: str = Depends(get_current_user)):
    result = cart_collection.delete_one({"user_id": user_id, "product_id": product_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Item not found in cart")
    return {"message": "Item removed from cart"}


# ===============================
# OPENFOODFACTS API INTEGRATION
# ===============================
@app.post("/products/sync-openfoodfacts")
def sync_openfoodfacts_products():
    """
    Syncs 200 products from Open Food Facts API to MongoDB with pagination.
    Fetches 2 pages of 100 products each.
    """
    try:
        # Check if sync is already in progress
        sync_status = api_sync_collection.find_one({"source": "openfoodfacts"})
        if sync_status and sync_status.get("status") == "in_progress":
            raise HTTPException(status_code=400, detail="Sync already in progress")
        
        # Mark sync as in progress
        api_sync_collection.update_one(
            {"source": "openfoodfacts"},
            {"$set": {"status": "in_progress", "started_at": datetime.utcnow().isoformat()}},
            upsert=True
        )
        
        base_url = "https://world.openfoodfacts.net/api/v2/search"
        headers = {"User-Agent": "MyGroceryApp/1.0 (contact@example.com)"}
        products_added = 0
        total_products = 0
        errors = []
        
        # Fetch 2 pages (100 products each = 200 total)
        for page in range(1, 3):
            max_retries = 3
            retry_count = 0
            success = False
            data = None
            
            while retry_count < max_retries and not success:
                try:
                    params = {
                        "fields": "code,product_name,brands,categories,image_front_url,energy-kcal_100g",
                        "page": page,
                        "page_size": 100
                    }
                    
                    print(f"Fetching page {page} (attempt {retry_count + 1}/{max_retries})...")
                    response = requests.get(base_url, params=params, headers=headers, timeout=15)
                    response.raise_for_status()
                    data = response.json()
                    success = True
                    print(f"Successfully fetched page {page}")
                    
                except requests.exceptions.Timeout:
                    retry_count += 1
                    error_msg = f"Page {page} - Timeout (attempt {retry_count}/{max_retries})"
                    print(error_msg)
                    errors.append(error_msg)
                    if retry_count < max_retries:
                        import time
                        time.sleep(2)  # Wait 2 seconds before retry
                    continue
                    
                except requests.exceptions.RequestException as e:
                    retry_count += 1
                    error_msg = f"Page {page} - {str(e)} (attempt {retry_count}/{max_retries})"
                    print(error_msg)
                    errors.append(error_msg)
                    if retry_count < max_retries:
                        import time
                        time.sleep(2)  # Wait 2 seconds before retry
                    continue
            
            # If fetch was unsuccessful after retries, skip this page
            if not success or data is None:
                continue
            
            products_list = data.get("products", [])
            
            for product_data in products_list:
                # Skip products without a name
                if not product_data.get("product_name"):
                    continue
                
                barcode = product_data.get("code", "")
                
                # Check if product already exists
                existing = products_collection.find_one({"barcode": barcode})
                if existing:
                    continue
                
                # Transform the data to match our schema
                product_to_insert = {
                    "name": product_data.get("product_name", "Unknown Product"),
                    "brand": product_data.get("brands", ""),
                    "description": product_data.get("categories", ""),
                    "price": round(random.uniform(1, 10), 2),  # Random price between 1-10 euros
                    "category": product_data.get("categories", "").split(",")[0] if product_data.get("categories") else "",
                    "stock": random.randint(75, 100),  # Random stock between 75-100
                    "barcode": barcode,
                    "source": "openfoodfacts",
                    "images": [product_data.get("image_front_url")] if product_data.get("image_front_url") else [],
                    "rating": 0.0,
                    "tags": [],
                    "created_at": datetime.utcnow().isoformat()
                }
                
                # Insert the product
                result = products_collection.insert_one(product_to_insert)
                if result.inserted_id:
                    products_added += 1
                
                total_products += 1
        
        # Mark sync as completed
        api_sync_collection.update_one(
            {"source": "openfoodfacts"},
            {"$set": {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "products_added": products_added,
                "total_products_processed": total_products,
                "errors": errors if errors else None
            }},
            upsert=True
        )
        
        return {
            "message": "OpenFoodFacts sync completed",
            "products_added": products_added,
            "total_products_processed": total_products,
            "pages_processed": 2,
            "errors": errors if errors else None,
            "status": "success" if products_added > 0 else "no_new_products"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        # Mark sync as failed
        error_detail = str(e)
        print(f"Sync failed with error: {error_detail}")
        api_sync_collection.update_one(
            {"source": "openfoodfacts"},
            {"$set": {
                "status": "failed",
                "error": error_detail,
                "failed_at": datetime.utcnow().isoformat()
            }},
            upsert=True
        )
        raise HTTPException(status_code=500, detail=f"Error syncing products: {error_detail}")


@app.get("/products/sync-status")
def get_sync_status():
    """
    Get the current sync status of OpenFoodFacts API.
    """
    sync_status = api_sync_collection.find_one({"source": "openfoodfacts"})
    if not sync_status:
        return {"status": "never_synced"}
    
    return {
        "source": sync_status.get("source"),
        "status": sync_status.get("status"),
        "started_at": sync_status.get("started_at"),
        "completed_at": sync_status.get("completed_at"),
        "products_added": sync_status.get("products_added", 0),
        "total_products_processed": sync_status.get("total_products_processed", 0),
        "error": sync_status.get("error")
    }


@app.get("/products/by-barcode/{barcode}", response_model=ProductResponse)
def get_product_by_barcode(barcode: str):
    """
    Get a product by its barcode (useful for products from OpenFoodFacts).
    """
    product = products_collection.find_one({"barcode": barcode})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product_helper(product)


@app.get("/kpi/total-users")
def total_users_kpi(current_user: str = Depends(get_current_user)):
    total_users = users_collection.count_documents({})
    return {"total_users": total_users}

@app.get("/kpi/total-products")
def total_products_kpi(current_user: str = Depends(get_current_user)):
    total_products = products_collection.count_documents({})
    return {"total_products": total_products}

@app.get("/kpi/total-invoices")
def total_invoices_kpi(current_user: str = Depends(get_current_user)):
    total_invoices = invoices_collection.count_documents({})
    return {"total_invoices": total_invoices}

@app.get("/kpi/total-revenue")
def total_revenue_kpi(current_user: str = Depends(get_current_user)):
    invoices = invoices_collection.find({})
    total_revenue = sum(invoice.get("total_amount", 0) for invoice in invoices)
    return {"total_revenue": total_revenue}

@app.get("/kpi/average-order-value")
def average_order_value_kpi(current_user: str = Depends(get_current_user)):
    total_invoices = invoices_collection.count_documents({})
    invoices = invoices_collection.find({})
    total_revenue = sum(invoice.get("total_amount", 0) for invoice in invoices)
    average_order_value = total_revenue / total_invoices if total_invoices > 0 else 0
    return {"average_order_value": average_order_value}

@app.get("/kpi/total-cart-items")
def total_cart_items_kpi(current_user: str = Depends(get_current_user)):
    total_cart_items = sum(item.get("quantity", 1) for item in cart_collection.find({}))
    return {"total_cart_items": total_cart_items}

@app.get("/kpi/total-wishlist-items")
def total_wishlist_items_kpi(current_user: str = Depends(get_current_user)):
    total_wishlist_items = wishlist_collection.count_documents({})
    return {"total_wishlist_items": total_wishlist_items}

@app.get("/kpi/active-customers")
def active_customers_kpi(current_user: str = Depends(get_current_user)):
    active_customer_ids = invoices_collection.distinct("user_id")
    active_customers = len(active_customer_ids)
    return {"active_customers": active_customers}

@app.get("/kpi/top-selling-products")
def top_selling_products_kpi(current_user: str = Depends(get_current_user)):
    product_counts = {}
    for invoice in invoices_collection.find({}):
        for item in invoice.get("products", []):
            pid = item["product_id"]
            product_counts[pid] = product_counts.get(pid, 0) + item.get("quantity", 1)
    top_selling_products = sorted(
        [{"product_id": pid, "quantity_sold": qty} for pid, qty in product_counts.items()],
        key=lambda x: x["quantity_sold"],
        reverse=True
    )[:5]  # top 5 products
    return {"top_selling_products": top_selling_products}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
