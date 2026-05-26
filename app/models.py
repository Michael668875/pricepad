from app.extensions import db
from datetime import datetime, timezone
from sqlalchemy.sql import func

# --------------------------
# Canonical ThinkPad models
# --------------------------
class ThinkPadModel(db.Model):
    __tablename__ = "model_list"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    slug = db.Column(db.String, unique=True, nullable=False)

    # backref for models linked to this canonical model
    models = db.relationship("Model", back_populates="canon_model", cascade="all, delete-orphan")


# --------------------------
# Parsed Model from eBay
# --------------------------
class Model(db.Model):
    __tablename__ = "models"

    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column( # removed model_id from listings to avoid circular logic
        db.Integer,
        db.ForeignKey("listings.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True
    )

    name = db.Column(db.String, nullable=True, index=True)  # final model name

    # Link to canonical model (optional, for browser display)
    canon_model_id = db.Column(db.Integer, db.ForeignKey("model_list.id", ondelete="SET NULL"), index=True)
    canon_model = db.relationship("ThinkPadModel", back_populates="models", lazy="joined")

    # Listings associated with this model
    listing = db.relationship("Listing", back_populates="model", uselist=False)
    stats = db.relationship("ModelPriceStats", back_populates="model", uselist=False, cascade="all, delete-orphan", single_parent=True)


# --------------------------
# Listings
# --------------------------
class Listing(db.Model):
    __tablename__ = "listings"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.String(20), nullable=True)
    ebay_item_id = db.Column(db.String, unique=True, nullable=False, index=True)
    title = db.Column(db.String)
    price = db.Column(db.Numeric(10, 2))
    currency = db.Column(db.String(10), nullable=False)
    condition = db.Column(db.String)
    listing_type = db.Column(db.String(50))
    marketplace = db.Column(db.String)
    item_country = db.Column(db.String(2))
    item_url = db.Column(db.Text)
    affiliate_url = db.Column(db.Text)
    status = db.Column(db.String, default="ACTIVE", server_default="ACTIVE", index=True)
    first_seen = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    miss_count = db.Column(db.Integer, nullable=False, default=0)
    ended_at = db.Column(db.DateTime, nullable=True)
    last_updated = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Link to parsed model
    model = db.relationship(
        "Model",
        back_populates="listing",
        uselist=False,
        cascade="all, delete-orphan",
        single_parent=True
    )

    # Relationships
    price_history = db.relationship("PriceHistory", back_populates="listing", cascade="all, delete-orphan")
    specs = db.relationship("Specs", back_populates="listing", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        db.Index("idx_listings_marketplace_status_price", "marketplace", "status", "price"),
    )

    @property
    def is_active(self):
        return self.status == "ACTIVE" and self.ended_at is None

# --------------------------
# Specs
# --------------------------
class Specs(db.Model):
    __tablename__ = "specs"

    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listings.id", ondelete="CASCADE"), unique=True, index=True)

    cpu = db.Column(db.Text)
    cpu_freq = db.Column(db.Text)
    ram = db.Column(db.Float)
    storage = db.Column(db.Float)
    storage_type = db.Column(db.Text)
    screen_size = db.Column(db.Text)
    display = db.Column(db.Text)
    gpu = db.Column(db.Text)
    os = db.Column(db.Text)
    raw_ram = db.Column(db.Text)
    raw_storage = db.Column(db.Text)
    raw_storage_type = db.Column(db.Text)
    ram_processed = db.Column(db.Boolean, default=False, nullable=False)
    storage_processed = db.Column(db.Boolean, default=False, nullable=False)
    storage_type_processed = db.Column(db.Boolean, default=False, nullable=False)

    listing = db.relationship("Listing", back_populates="specs")

    __table_args__ = (
        db.Index("idx_specs_search", "cpu", "ram", "storage"),
    )

# --------------------------
# Price History
# --------------------------
class PriceHistory(db.Model):
    __tablename__ = "price_history"

    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listings.id", ondelete="CASCADE"))
    price = db.Column(db.Numeric(10, 2))
    currency = db.Column(db.String(10), nullable=False)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    listing = db.relationship("Listing", back_populates="price_history")


# --------------------------
# Temp tables (for API fetch)
# --------------------------
class TempSummaries(db.Model):
    __tablename__ = "temp_summaries"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.String(20))

    ebay_item_id = db.Column(db.String, unique=True, nullable=False)
    title = db.Column(db.Text)
    price = db.Column(db.Numeric(10, 2))
    currency = db.Column(db.String(10), nullable=False)
    condition = db.Column(db.String)
    listing_type = db.Column(db.String(50))
    marketplace = db.Column(db.String)
    item_country = db.Column(db.String(2))
    item_url = db.Column(db.Text)
    affiliate_url = db.Column(db.Text)
    creation_date = db.Column(db.DateTime(timezone=True))
    first_seen = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    sold_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_updated = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class TempDetails(db.Model):
    __tablename__ = "temp_details"

    id = db.Column(db.Integer, primary_key=True)
    ebay_item_id = db.Column(db.Text, unique=True, nullable=False)
    cpu = db.Column(db.Text)
    cpu_freq = db.Column(db.Text)
    ram = db.Column(db.Text)
    storage = db.Column(db.Text)
    storage_type = db.Column(db.Text)
    screen_size = db.Column(db.Text)
    display = db.Column(db.Text)
    gpu = db.Column(db.Text)
    os = db.Column(db.Text)
    model = db.Column(db.Text)
    mpn = db.Column(db.Text) # add as backup model
    seller_username = db.Column(db.Text)
    seller_feedback_score = db.Column(db.Integer)
    seller_feedback_percent = db.Column(db.Numeric(5, 2))


# --------------------------
# Price Stats
# --------------------------
class ModelPriceStats(db.Model):
    __tablename__ = "model_price_stats"

    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey("models.id"), nullable=False, unique=True, index=True)
    marketplace = db.Column(db.String)
    avg_price = db.Column(db.Numeric(10, 2))
    min_price = db.Column(db.Numeric(10, 2))
    max_price = db.Column(db.Numeric(10, 2))
    listing_count = db.Column(db.Integer)
    updated_at = db.Column(db.DateTime(timezone=True))

    model = db.relationship("Model", back_populates="stats")

    __table_args__ = (
        db.UniqueConstraint("model_id", "marketplace", name="ix_model_price_stats_model_market"),
    )


class Marketplace(db.Model):
    __tablename__ = "marketplaces"

    id = db.Column(db.Integer, primary_key=True)
    country_code = db.Column(db.String(10), nullable=False)   # "US"
    marketplace_id = db.Column(db.String(20), nullable=False, unique=True)  # "EBAY_US"
    enabled = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<Marketplace {self.marketplace_id}>"

class CPU(db.Model):
    __tablename__ = "cpu"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    cpu_num = db.Column(db.Text)

class RAM(db.Model):
    __tablename__ = "ram"
    id = db.Column(db.Integer, primary_key=True)
    size = db.Column(db.String(20), unique=True, nullable=False)

class Storage(db.Model):
    __tablename__ = "storage"
    id = db.Column(db.Integer, primary_key=True)
    size = db.Column(db.String(20), unique=True, nullable=False)


class Blacklist(db.Model):
    __tablename__ = "blacklist"

    id = db.Column(db.Integer, primary_key=True)
    phrase = db.Column(db.String(255), unique=True, nullable=False, index=True)