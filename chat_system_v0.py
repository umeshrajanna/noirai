#!/usr/bin/env python3
"""
High-Performance Chat System - With Real-Time Search
Complete implementation with modern redis.asyncio and SerpAPI integration
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, DateTime, Text, Boolean, Integer, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
import json
import asyncio
# FIXED: Use modern redis with async support instead of deprecated aioredis
import redis.asyncio as aioredis
import logging
from contextlib import asynccontextmanager
import anthropic
import os
from dotenv import load_dotenv
import aiohttp
from serpapi import GoogleSearch

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://chatuser:chatpass123@localhost/chatdb")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

# Helper function for web search
async def search_web(query: str) -> str:
    """Search the web using SerpAPI"""
    if not SERPAPI_KEY or SERPAPI_KEY == "":
        return "Search unavailable: API key not configured"
    
    try:
        search = GoogleSearch({
            "q": query,
            "api_key": SERPAPI_KEY,
            "num": 5
        })
        results = search.get_dict()
        
        if "organic_results" in results:
            snippets = []
            for i, result in enumerate(results["organic_results"][:3], 1):
                title = result.get("title", "")
                snippet = result.get("snippet", "")
                link = result.get("link", "")
                snippets.append(f"{i}. {title}\n{snippet}\n{link}")
            return "\n\n".join(snippets)
        return "No results found"
    except Exception as e:
        logger.error(f"Search error: {e}")
        return f"Search error: {str(e)}"

# Helper function to check if query needs search
def needs_search(query: str) -> bool:
    """Determine if query needs real-time search"""
    search_keywords = [
        "weather", "current", "today", "now", "latest", "recent",
        "news", "price", "stock", "score", "result", "update",
        "who won", "what's happening", "breaking"
    ]
    query_lower = query.lower()
    return any(keyword in query_lower for keyword in search_keywords)

# Database setup
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String, nullable=False)
    title = Column(String, default="New Conversation")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"))
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    conversation = relationship("Conversation", back_populates="messages")

# Create tables
Base.metadata.create_all(bind=engine)

# Pydantic models
class MessageCreate(BaseModel):
    content: str

class ConversationCreate(BaseModel):
    user_id: str
    title: Optional[str] = "New Conversation"

class ConversationResponse(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Redis connection manager
class RedisManager:
    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
    
    async def connect(self):
        try:
            # Modern redis.asyncio connection
            self.redis = await aioredis.from_url(
                REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5
            )
            await self.redis.ping()
            logger.info("✅ Redis connected successfully")
        except Exception as e:
            logger.error(f"❌ Redis connection failed: {e}")
            self.redis = None
    
    async def get(self, key: str) -> Optional[str]:
        if not self.redis:
            return None
        try:
            return await self.redis.get(key)
        except Exception as e:
            logger.error(f"Redis GET error: {e}")
            return None
    
    async def set(self, key: str, value: str, ex: int = 3600):
        if not self.redis:
            return
        try:
            await self.redis.set(key, value, ex=ex)
        except Exception as e:
            logger.error(f"Redis SET error: {e}")
    
    async def close(self):
        if self.redis:
            await self.redis.close()

redis_manager = RedisManager()

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
        logger.info(f"User {user_id} connected")
    
    def disconnect(self, websocket: WebSocket, user_id: str):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        logger.info(f"User {user_id} disconnected")
    
    async def send_message(self, message: str, user_id: str):
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_text(message)
                except:
                    pass

connection_manager = ConnectionManager()

# Database dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# FastAPI app
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await redis_manager.connect()
    logger.info("✅ Chat system started")
    yield
    # Shutdown
    await redis_manager.close()
    logger.info("👋 Chat system stopped")

app = FastAPI(title="Chat System API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Endpoints
@app.post("/conversations", response_model=ConversationResponse)
async def create_conversation(conv: ConversationCreate, db: Session = Depends(get_db)):
    try:
        new_conv = Conversation(user_id=conv.user_id, title=conv.title)
        db.add(new_conv)
        db.commit()
        db.refresh(new_conv)
        return ConversationResponse(
            id=str(new_conv.id),
            user_id=new_conv.user_id,
            title=new_conv.title,
            created_at=new_conv.created_at,
            updated_at=new_conv.updated_at
        )
    except Exception as e:
        logger.error(f"Create conversation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/conversations/{user_id}", response_model=List[ConversationResponse])
async def get_conversations(user_id: str, db: Session = Depends(get_db)):
    try:
        convs = db.query(Conversation).filter(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc()).all()
        return [
            ConversationResponse(
                id=str(c.id),
                user_id=c.user_id,
                title=c.title,
                created_at=c.created_at,
                updated_at=c.updated_at
            ) for c in convs
        ]
    except Exception as e:
        logger.error(f"Get conversations error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, msg: MessageCreate, db: Session = Depends(get_db)):
    try:
        conv = db.query(Conversation).filter(Conversation.id == uuid.UUID(conversation_id)).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        # Save user message
        user_msg = Message(conversation_id=uuid.UUID(conversation_id), role="user", content=msg.content)
        db.add(user_msg)
        db.commit()
        
        # Get conversation history
        messages = db.query(Message).filter(Message.conversation_id == uuid.UUID(conversation_id)).order_by(Message.created_at).all()
        history = [{"role": m.role, "content": m.content} for m in messages]
        
        # Check if we need to search
        search_results = None
        if needs_search(msg.content):
            logger.info(f"🔍 Searching for: {msg.content}")
            search_results = await search_web(msg.content)
            logger.info(f"📊 Search results obtained: {len(search_results)} chars")
        
        # Call Claude API
        if CLAUDE_API_KEY:
            try:
                # Add search results to the message if available
                if search_results:
                    enhanced_content = f"{msg.content}\n\n[Real-Time Search Results]:\n{search_results}"
                    # Update the last message in history with search results
                    history[-1]["content"] = enhanced_content
                
                client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=2048,
                    system="You are a helpful AI assistant with access to real-time information through search results. When search results are provided, use them to give accurate, current information. Always cite your sources when using search results.",
                    messages=history
                )
                assistant_content = response.content[0].text
                
                # Save assistant message
                assistant_msg = Message(conversation_id=uuid.UUID(conversation_id), role="assistant", content=assistant_content)
                db.add(assistant_msg)
                conv.updated_at = datetime.now(timezone.utc)
                db.commit()
                
                # Send via WebSocket
                await connection_manager.send_message(
                    json.dumps({"type": "message", "role": "assistant", "content": assistant_content}),
                    conv.user_id
                )
                
                return {"role": "assistant", "content": assistant_content, "searched": search_results is not None}
            except Exception as e:
                logger.error(f"Claude API error: {e}")
                return {"role": "assistant", "content": f"Error: {str(e)}"}
        else:
            return {"role": "assistant", "content": "Claude API key not configured"}
        
    except Exception as e:
        logger.error(f"Send message error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, db: Session = Depends(get_db)):
    try:
        messages = db.query(Message).filter(Message.conversation_id == uuid.UUID(conversation_id)).order_by(Message.created_at).all()
        return [{
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat()
        } for m in messages]
    except Exception as e:
        logger.error(f"Get messages error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await connection_manager.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connection_manager.disconnect(websocket, user_id)

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "database": "connected",
        "redis": "connected" if redis_manager.redis else "disconnected",
        "serpapi": "configured" if SERPAPI_KEY else "not configured",
        "claude": "configured" if CLAUDE_API_KEY else "not configured",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/")
async def root():
    return {
        "message": "Enhanced Chat System API with Real-Time Search",
        "docs": "/docs",
        "health": "/health",
        "features": ["Claude AI", "Real-time Search", "WebSockets", "Conversation Memory"]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")