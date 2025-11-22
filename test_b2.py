import asyncio
import os
from app.utils.storage_backend import get_storage

async def test_b2():
    # Mock the current_app config for testing
    class MockApp:
        config = {
            'STORAGE_VENDOR': os.getenv('STORAGE_VENDOR', 'local'),
            'S3_ENDPOINT_URL': os.getenv('S3_ENDPOINT_URL'),
            'S3_ACCESS_KEY_ID': os.getenv('S3_ACCESS_KEY_ID'),
            'S3_SECRET_ACCESS_KEY': os.getenv('S3_SECRET_ACCESS_KEY'),
            'S3_BUCKET': os.getenv('S3_BUCKET'),
            'S3_REGION': os.getenv('S3_REGION'),
            'S3_FORCE_PATH_STYLE': True,
        }
    
    # Monkey patch for testing
    import app.utils.storage_backend
    app.utils.storage_backend.current_app = MockApp()
    
    storage = get_storage()
    print(f"Using storage backend: {type(storage).__name__}")
    
    # Test upload
    test_data = b"Hello BackBlaze B2!"
    test_key = "test/hello.txt"
    
    try:
        await storage.put_bytes(test_key, test_data, "text/plain")
        print("✅ Upload successful")
        
        # Test download
        data, content_type = await storage.get_bytes(test_key)
        print(f"✅ Download successful: {data.decode()} (type: {content_type})")
        
        # Test delete
        await storage.delete(test_key)
        print("✅ Delete successful")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_b2())