from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=255)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ProductOut(BaseModel):
    id: int
    asin: str | None
    title: str
    slug: str
    description: str
    category: str
    tags: list[str]
    price: float
    level: str | None
    image_url: str | None
    product_url: str | None
    stars: float | None
    reviews: int | None
    is_best_seller: bool
    bought_in_last_month: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EventIn(BaseModel):
    event_type: str = Field(max_length=50)
    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: str | None = Field(default=None, max_length=255)
    payload: dict = Field(default_factory=dict)


class EventBatchIn(BaseModel):
    events: list[EventIn] = Field(max_length=100)


class CartAddIn(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=99)


class CartUpdateIn(BaseModel):
    product_id: int
    quantity: int = Field(ge=0, le=99)


class CartRemoveIn(BaseModel):
    product_id: int


class RecommendationItemOut(BaseModel):
    product_id: int
    rank: int
    score: float | None
    rationale: str

    model_config = {"from_attributes": True}


class RecommendationOut(BaseModel):
    id: int
    narrative: str
    summary: str
    trigger_reason: str | None
    source: str
    created_at: datetime
    items: list[RecommendationItemOut]

    model_config = {"from_attributes": True}
