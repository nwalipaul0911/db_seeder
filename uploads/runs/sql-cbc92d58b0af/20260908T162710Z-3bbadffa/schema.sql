-- SQLite Test Schema
-- E-commerce / Marketplace
--
-- Recommended:
-- PRAGMA foreign_keys = ON;

PRAGMA foreign_keys = ON;

-- ============================================================
-- USERS
-- ============================================================

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,

    role TEXT NOT NULL DEFAULT 'customer'
        CHECK (role IN ('customer', 'seller', 'admin')),

    phone TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
        CHECK (is_active IN (0, 1)),

    date_of_birth TEXT,

    -- SQLite has no native JSON type.
    -- JSON is commonly stored as TEXT.
    metadata TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_role
    ON users(role);

CREATE INDEX idx_users_created_at
    ON users(created_at);

CREATE INDEX idx_users_name
    ON users(last_name, first_name);

-- ============================================================
-- ADDRESSES
-- ============================================================

CREATE TABLE addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,

    address_line_1 TEXT NOT NULL,
    address_line_2 TEXT,
    city TEXT NOT NULL,
    state TEXT,
    postal_code TEXT NOT NULL,
    country TEXT NOT NULL DEFAULT 'Nigeria',

    is_default INTEGER NOT NULL DEFAULT 0
        CHECK (is_default IN (0, 1)),

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_addresses_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);

CREATE INDEX idx_addresses_user_id
    ON addresses(user_id);

-- ============================================================
-- CATEGORIES
-- ============================================================

CREATE TABLE categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,

    -- Self-referencing relationship
    parent_id INTEGER,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_categories_parent
        FOREIGN KEY (parent_id)
        REFERENCES categories(id)
        ON DELETE SET NULL
);

CREATE INDEX idx_categories_parent_id
    ON categories(parent_id);

-- ============================================================
-- PRODUCTS
-- ============================================================

CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    seller_id INTEGER NOT NULL,
    category_id INTEGER,

    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,

    -- SQLite does not enforce DECIMAL precision/scale
    -- like PostgreSQL/MySQL do.
    price NUMERIC NOT NULL,

    quantity INTEGER NOT NULL DEFAULT 0,

    weight_kg NUMERIC,

    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (
            status IN (
                'draft',
                'active',
                'out_of_stock',
                'archived'
            )
        ),

    attributes TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_products_seller
        FOREIGN KEY (seller_id)
        REFERENCES users(id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_products_category
        FOREIGN KEY (category_id)
        REFERENCES categories(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_products_price
        CHECK (price >= 0),

    CONSTRAINT chk_products_quantity
        CHECK (quantity >= 0),

    CONSTRAINT chk_products_weight
        CHECK (
            weight_kg IS NULL
            OR weight_kg > 0
        )
);

CREATE INDEX idx_products_seller_id
    ON products(seller_id);

CREATE INDEX idx_products_category_id
    ON products(category_id);

CREATE INDEX idx_products_status
    ON products(status);

CREATE INDEX idx_products_price
    ON products(price);

CREATE INDEX idx_products_created_at
    ON products(created_at);

-- ============================================================
-- PRODUCT REVIEWS
-- ============================================================

CREATE TABLE product_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    product_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,

    rating INTEGER NOT NULL,

    title TEXT,
    body TEXT,

    verified_purchase INTEGER NOT NULL DEFAULT 0
        CHECK (verified_purchase IN (0, 1)),

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_reviews_product
        FOREIGN KEY (product_id)
        REFERENCES products(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_reviews_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,

    CONSTRAINT chk_reviews_rating
        CHECK (rating BETWEEN 1 AND 5),

    CONSTRAINT uq_product_user_review
        UNIQUE (product_id, user_id)
);

CREATE INDEX idx_reviews_product_id
    ON product_reviews(product_id);

CREATE INDEX idx_reviews_user_id
    ON product_reviews(user_id);

-- ============================================================
-- ORDERS
-- ============================================================

CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    user_id INTEGER NOT NULL,
    shipping_address_id INTEGER,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'paid',
                'shipped',
                'delivered',
                'cancelled'
            )
        ),

    subtotal NUMERIC NOT NULL DEFAULT 0,
    shipping_fee NUMERIC NOT NULL DEFAULT 0,
    tax NUMERIC NOT NULL DEFAULT 0,
    total NUMERIC NOT NULL DEFAULT 0,

    notes TEXT,

    placed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_orders_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_orders_address
        FOREIGN KEY (shipping_address_id)
        REFERENCES addresses(id)
        ON DELETE SET NULL,

    CONSTRAINT chk_orders_subtotal
        CHECK (subtotal >= 0),

    CONSTRAINT chk_orders_shipping
        CHECK (shipping_fee >= 0),

    CONSTRAINT chk_orders_tax
        CHECK (tax >= 0),

    CONSTRAINT chk_orders_total
        CHECK (total >= 0)
);

CREATE INDEX idx_orders_user_id
    ON orders(user_id);

CREATE INDEX idx_orders_address_id
    ON orders(shipping_address_id);

CREATE INDEX idx_orders_status
    ON orders(status);

CREATE INDEX idx_orders_created_at
    ON orders(created_at);

-- ============================================================
-- ORDER ITEMS
-- Composite primary key
-- ============================================================

CREATE TABLE order_items (
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,

    quantity INTEGER NOT NULL,
    unit_price NUMERIC NOT NULL,

    PRIMARY KEY (order_id, product_id),

    CONSTRAINT fk_order_items_order
        FOREIGN KEY (order_id)
        REFERENCES orders(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_order_items_product
        FOREIGN KEY (product_id)
        REFERENCES products(id)
        ON DELETE RESTRICT,

    CONSTRAINT chk_order_items_quantity
        CHECK (quantity > 0),

    CONSTRAINT chk_order_items_price
        CHECK (unit_price >= 0)
);

CREATE INDEX idx_order_items_product_id
    ON order_items(product_id);

-- ============================================================
-- PAYMENTS
-- ============================================================

CREATE TABLE payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    order_id INTEGER NOT NULL,

    amount NUMERIC NOT NULL,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'completed',
                'failed',
                'refunded'
            )
        ),

    provider TEXT,
    transaction_reference TEXT UNIQUE,

    paid_at TEXT,

    metadata TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_payments_order
        FOREIGN KEY (order_id)
        REFERENCES orders(id)
        ON DELETE RESTRICT,

    CONSTRAINT chk_payments_amount
        CHECK (amount > 0)
);

CREATE INDEX idx_payments_order_id
    ON payments(order_id);

CREATE INDEX idx_payments_status
    ON payments(status);

-- ============================================================
-- WISHLISTS
-- ============================================================

CREATE TABLE wishlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    user_id INTEGER NOT NULL,

    name TEXT NOT NULL DEFAULT 'My Wishlist',

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_wishlists_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE,

    CONSTRAINT uq_user_wishlist_name
        UNIQUE (user_id, name)
);

CREATE INDEX idx_wishlists_user_id
    ON wishlists(user_id);

-- ============================================================
-- WISHLIST ITEMS
-- Composite primary key
-- ============================================================

CREATE TABLE wishlist_items (
    wishlist_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,

    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (wishlist_id, product_id),

    CONSTRAINT fk_wishlist_items_wishlist
        FOREIGN KEY (wishlist_id)
        REFERENCES wishlists(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_wishlist_items_product
        FOREIGN KEY (product_id)
        REFERENCES products(id)
        ON DELETE CASCADE
);

CREATE INDEX idx_wishlist_items_product_id
    ON wishlist_items(product_id);

-- ============================================================
-- PRODUCT TAGS
-- Many-to-many relationship
-- ============================================================

CREATE TABLE product_tags (
    product_id INTEGER NOT NULL,
    tag TEXT NOT NULL,

    PRIMARY KEY (product_id, tag),

    CONSTRAINT fk_product_tags_product
        FOREIGN KEY (product_id)
        REFERENCES products(id)
        ON DELETE CASCADE
);

CREATE INDEX idx_product_tags_tag
    ON product_tags(tag);