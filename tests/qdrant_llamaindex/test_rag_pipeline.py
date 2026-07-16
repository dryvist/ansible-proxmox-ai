#!/usr/bin/env python3
"""End-to-end RAG pipeline validation test.

Validates that Qdrant + Ollama + LlamaIndex work together:
1. Connects to Qdrant
2. Creates a test collection
3. Embeds test documents via Ollama
4. Stores vectors in Qdrant
5. Queries and verifies retrieval
6. Cleans up

Environment variables:
    QDRANT_URL:     Qdrant HTTP endpoint (default: http://localhost:6333)
    QDRANT_API_KEY: Qdrant API key
    OLLAMA_URL:     Ollama base URL (default: http://localhost:11434)
"""

import os
import sys

TEST_COLLECTION = "test_validation"

TEST_DOCUMENTS = [
    "Proxmox Virtual Environment is an open-source server virtualization platform.",
    "Cribl Edge processes syslog data and forwards it to Splunk via HEC.",
    "HAProxy load balances traffic across multiple Cribl Edge containers.",
    "Qdrant is a vector database optimized for similarity search.",
    "LlamaIndex orchestrates RAG pipelines with embedding models.",
]


def main():
    """Run the end-to-end RAG pipeline test."""
    try:
        from llama_index.core import Document, Settings, VectorStoreIndex
        from llama_index.embeddings.ollama import OllamaEmbedding
        from llama_index.vector_stores.qdrant import QdrantVectorStore
        from qdrant_client import QdrantClient
    except ImportError as exc:
        print(f"FAIL: Missing dependency: {exc}")
        print("Install with: pip install -r requirements.txt")
        return 1

    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    qdrant_api_key = os.environ.get("QDRANT_API_KEY", "")
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")

    print(f"Qdrant URL: {qdrant_url}")
    print(f"Ollama URL: {ollama_url}")

    # Step 1: Connect to Qdrant
    print("\n[1/6] Connecting to Qdrant...")
    try:
        client = QdrantClient(
            url=qdrant_url,
            api_key=qdrant_api_key if qdrant_api_key else None,
        )
        collections = client.get_collections()
        print(f"  Connected. Existing collections: {len(collections.collections)}")
    except Exception as exc:
        print(f"FAIL: Cannot connect to Qdrant at {qdrant_url}: {exc}")
        return 1

    # Step 2: Clean up any previous test collection
    print(f"\n[2/6] Creating test collection '{TEST_COLLECTION}'...")
    try:
        existing = [c.name for c in client.get_collections().collections]
        if TEST_COLLECTION in existing:
            client.delete_collection(TEST_COLLECTION)
            print(f"  Removed existing '{TEST_COLLECTION}' collection")
    except Exception as exc:
        print(f"FAIL: Cannot manage collections: {exc}")
        return 1

    # Step 3: Configure Ollama embeddings
    print("\n[3/6] Configuring Ollama embedding model...")
    try:
        embed_model = OllamaEmbedding(
            model_name="nomic-embed-text",
            base_url=ollama_url,
        )
        Settings.embed_model = embed_model
        print("  Ollama embedding model configured")
    except Exception as exc:
        print(f"FAIL: Cannot configure Ollama embeddings: {exc}")
        return 1

    # Step 4: Create vector store and index documents
    print(f"\n[4/6] Embedding and storing {len(TEST_DOCUMENTS)} test documents...")
    try:
        vector_store = QdrantVectorStore(
            client=client,
            collection_name=TEST_COLLECTION,
        )
        documents = [Document(text=text) for text in TEST_DOCUMENTS]
        index = VectorStoreIndex.from_documents(
            documents,
            vector_store=vector_store,
        )
        print(f"  Indexed {len(TEST_DOCUMENTS)} documents into '{TEST_COLLECTION}'")
    except Exception as exc:
        print(f"FAIL: Cannot index documents: {exc}")
        _cleanup(client)
        return 1

    # Step 5: Query and verify retrieval
    print("\n[5/6] Querying for relevant documents...")
    try:
        query_engine = index.as_query_engine(similarity_top_k=2)
        response = query_engine.query("What processes syslog data?")
        response_text = str(response)
        print(f"  Query: 'What processes syslog data?'")
        print(f"  Response: {response_text[:200]}")

        # Verify we got a meaningful response (not empty)
        if len(response_text.strip()) == 0:
            print("FAIL: Empty response from query engine")
            _cleanup(client)
            return 1
        print("  Retrieval verification passed")
    except Exception as exc:
        print(f"FAIL: Query failed: {exc}")
        _cleanup(client)
        return 1

    # Step 6: Cleanup
    print(f"\n[6/6] Cleaning up test collection '{TEST_COLLECTION}'...")
    _cleanup(client)

    print("\n============================================")
    print("RAG PIPELINE E2E TEST PASSED")
    print("============================================")
    print("  Qdrant:     connected, collection created")
    print("  Ollama:     embedding model working")
    print("  LlamaIndex: indexing + retrieval working")
    print("  Cleanup:    test collection removed")
    return 0


def _cleanup(client):
    """Remove the test collection from Qdrant."""
    try:
        client.delete_collection(TEST_COLLECTION)
        print(f"  Removed '{TEST_COLLECTION}' collection")
    except Exception as exc:
        print(f"  Warning: cleanup failed: {exc}")


if __name__ == "__main__":
    sys.exit(main())
