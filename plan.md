Yes. For a **database seeding app**, I’d define “5 most used schemas” as the five input formats that give you the broadest developer coverage—not five database *models*.

My recommended order is:

1. **SQL DDL**
2. **Prisma Schema**
3. **DBML**
4. **SQLAlchemy**
5. **JSON Schema / custom JSON**

The key is to **avoid building five independent seeding systems**. Build five parsers that all produce the same internal representation.

---

# 1. Target architecture

```text
                 INPUT FORMATS
                      │
       ┌──────────────┼──────────────┐
       │              │              │
     SQL DDL       Prisma          DBML
       │              │              │
       ├──────── SQLAlchemy ─────────┤
       │              │              │
       └──────── JSON Schema ────────┘
                      │
                      ▼
             ┌─────────────────┐
             │ Schema IR        │
             │                 │
             │ Tables          │
             │ Columns         │
             │ Types           │
             │ PKs             │
             │ FKs             │
             │ Indexes         │
             │ Constraints     │
             │ Enums           │
             │ Relationships   │
             └────────┬────────┘
                      │
                      ▼
             Schema Normalization
                      │
                      ▼
             Dependency Analysis
                      │
                      ▼
             Synthetic Data Model
                      │
                      ▼
             Constraint Resolution
                      │
                      ▼
                 Seed Engine
```

Your existing DBML implementation becomes just **one adapter**.

---

# 2. Define a canonical Schema IR first

This should be the first engineering task.

For example:

```python
class Schema:
    tables: list[Table]
    enums: list[Enum]
    relationships: list[Relationship]
    metadata: SchemaMetadata


class Table:
    name: str
    columns: list[Column]
    primary_key: PrimaryKey | None
    indexes: list[Index]
    constraints: list[Constraint]


class Column:
    name: str
    data_type: DataType
    nullable: bool
    default: DefaultValue | None
    unique: bool
    generated: bool
    references: ForeignKey | None


class Relationship:
    source_table: str
    source_columns: list[str]
    target_table: str
    target_columns: list[str]
    cardinality: Cardinality
```

The important principle:

> **Parsers understand syntax. The seeding engine understands schemas.**

Don't let the seeder know whether a relationship came from Prisma, SQL, or DBML.

---

# 3. Build a capability matrix

Before implementing parsers, decide what each format can express.

For example:

| Feature            |      SQL |   Prisma |     DBML | SQLAlchemy |     JSON |
| ------------------ | -------: | -------: | -------: | ---------: | -------: |
| Tables             |        ✅ |        ✅ |        ✅ |          ✅ |        ✅ |
| Columns            |        ✅ |        ✅ |        ✅ |          ✅ |        ✅ |
| Primary keys       |        ✅ |        ✅ |        ✅ |          ✅ |        ✅ |
| Foreign keys       |        ✅ |        ✅ |        ✅ |          ✅ |        ✅ |
| Composite PK       |        ✅ |        ✅ |        ✅ |          ✅ |        ✅ |
| Unique constraints |        ✅ |        ✅ |        ✅ |          ✅ |        ✅ |
| Indexes            |        ✅ |        ✅ |        ✅ |          ✅ |        ✅ |
| Enums              |        ✅ |        ✅ |        ✅ |          ✅ |        ✅ |
| Defaults           |        ✅ |        ✅ |        ✅ |          ✅ |        ✅ |
| Check constraints  |        ✅ |  Partial |  Partial |          ✅ |   Custom |
| Relationships      | Explicit | Explicit | Explicit |   Explicit | Explicit |
| Cascades           |        ✅ |        ✅ |        ✅ |          ✅ |   Custom |
| Generated columns  |        ✅ |  Partial |  Partial |          ✅ |   Custom |
| Views              |        ✅ |  Partial |  Partial |          ✅ |   Custom |

This becomes your **normalization contract**.

If an input format cannot represent something, don't invent it.

Instead:

```text
supported
unsupported
partially_supported
lossy
```

That distinction will matter enormously for debugging.

---

# 4. Input #1 — SQL DDL

### Priority: ⭐⭐⭐⭐⭐

This should probably be your most important parser.

Users can provide:

```sql
CREATE TABLE users (
    id BIGINT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    total DECIMAL(12,2),

    FOREIGN KEY (user_id)
        REFERENCES users(id)
);
```

Normalize it into your IR.

### Support initially

* `CREATE TABLE`
* column types
* `PRIMARY KEY`
* `FOREIGN KEY`
* `UNIQUE`
* `NOT NULL`
* `DEFAULT`
* indexes
* composite keys
* enums where supported
* `CHECK`
* `ON DELETE`
* `ON UPDATE`

### Important

Don't write a SQL parser yourself.

Use an existing SQL parser and normalize its AST.

Eventually support dialects:

```text
PostgreSQL
MySQL
SQLite
SQL Server
```

Start with **PostgreSQL + MySQL + SQLite**.

---

# 5. Input #2 — Prisma

### Priority: ⭐⭐⭐⭐⭐

Prisma is extremely useful because its schema is already highly structured.

Example:

```prisma
model User {
  id        Int      @id @default(autoincrement())
  email     String   @unique
  orders    Order[]
  createdAt DateTime @default(now())
}

model Order {
  id     Int  @id @default(autoincrement())
  userId Int
  user   User @relation(fields: [userId], references: [id])
}
```

Your parser should understand:

```text
model
field
type
optional
array
@id
@@id
@unique
@@unique
@index
@@index
@default
@relation
enum
```

Then:

```text
Prisma AST
    ↓
Prisma Adapter
    ↓
Schema IR
```

Don't generate seed data directly from Prisma.

---

# 6. Input #3 — DBML

### Priority: ⭐⭐⭐⭐

You already have this.

Keep it, but refactor it to conform to the IR.

Your current pipeline may look like:

```text
DBML
 ↓
DBML parser
 ↓
Seeder
```

Change it to:

```text
DBML
 ↓
DBML parser
 ↓
Schema IR
 ↓
Seeder
```

That makes DBML your first implementation of the architecture rather than something you eventually have to rewrite.

---

# 7. Input #4 — SQLAlchemy

### Priority: ⭐⭐⭐⭐

This is particularly valuable for Python applications.

Example:

```python
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)

    orders = relationship("Order")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    user_id = Column(ForeignKey("users.id"))
```

There are two approaches.

### Option A — Static source parsing

Parse Python source:

```text
.py
 ↓
Python AST
 ↓
SQLAlchemy model extraction
 ↓
Schema IR
```

### Option B — Runtime SQLAlchemy inspection

If the user's application can load the models:

```python
Base.metadata
       ↓
SQLAlchemy MetaData
       ↓
Schema IR
```

**I'd strongly prefer runtime metadata inspection where possible.**

It is substantially more reliable than trying to understand arbitrary Python source.

---

# 8. Input #5 — JSON Schema

### Priority: ⭐⭐⭐⭐

This gives you a generic machine-readable format.

Example:

```json
{
  "title": "User",
  "type": "object",
  "properties": {
    "id": {
      "type": "integer"
    },
    "email": {
      "type": "string",
      "format": "email"
    }
  },
  "required": ["id", "email"]
}
```

However, there is an important difference:

**JSON Schema isn't inherently relational.**

So don't pretend that:

```json
User → Order
```

exists unless your extension explicitly describes the relationship.

I'd therefore define a small relational extension:

```json
{
  "tables": {
    "users": {
      "columns": {},
      "primaryKey": ["id"]
    },
    "orders": {
      "columns": {},
      "foreignKeys": [
        {
          "columns": ["user_id"],
          "references": {
            "table": "users",
            "columns": ["id"]
          }
        }
      ]
    }
  }
}
```

You could call this something like:

```text
Seed Schema JSON
```

rather than pretending it's standard JSON Schema.

---

# 9. Create an adapter interface

This is probably the most important implementation decision.

```python
class SchemaAdapter(Protocol):

    def detect(self, source: str) -> bool:
        ...

    def parse(self, source: str) -> Schema:
        ...

    def capabilities(self) -> Capabilities:
        ...
```

Then:

```python
class DBMLAdapter(SchemaAdapter):
    ...


class SQLAdapter(SchemaAdapter):
    ...


class PrismaAdapter(SchemaAdapter):
    ...


class SQLAlchemyAdapter(SchemaAdapter):
    ...


class JSONSchemaAdapter(SchemaAdapter):
    ...
```

Your application becomes:

```python
schema = SchemaLoader.load(input)

result = Seeder(schema).generate()
```

The seeder doesn't care where it came from.

---

# 10. Add automatic format detection

Eventually users should be able to give you:

```bash
seed input.dbml
```

or:

```bash
seed schema.prisma
```

or:

```bash
seed schema.sql
```

or even:

```bash
seed schema.json
```

And the engine detects:

```text
.dbml       → DBML
.prisma     → Prisma
.sql        → SQL
.py         → SQLAlchemy
.json       → JSON Schema
```

But don't rely exclusively on extensions.

For example:

```text
stdin
API request
uploaded text
database metadata
```

may have no filename.

Use:

```text
extension
    ↓
content sniffing
    ↓
adapter detection
    ↓
parser
```

---

# 11. Add a schema validation layer

After parsing:

```text
Input
 ↓
Parser
 ↓
Schema IR
 ↓
Validation
```

Validate things such as:

```text
✓ duplicate tables
✓ duplicate columns
✓ missing referenced tables
✓ missing referenced columns
✓ invalid FK types
✓ circular dependencies
✓ invalid composite keys
✓ invalid indexes
✓ unsupported features
```

Return useful errors:

```text
SchemaError

Table: orders
Column: user_id

Foreign key references:
    users.uuid

But users.uuid does not exist.

Did you mean:
    users.id
```

This will make the app feel dramatically more polished.

---

# 12. Preserve source information

One thing I'd strongly recommend adding to your IR:

```python
class SchemaNode:
    source_format: str
    source_location: SourceLocation
```

So an error can say:

```text
Invalid foreign key in orders.user_id

Source:
schema.prisma:27
```

or:

```text
schema.sql:84
```

or:

```text
schema.dbml:142
```

This becomes especially useful once users start importing large schemas.

---

# 13. Normalize types

Different formats call the same thing different names.

For example:

```text
SQL             Prisma          DBML

BIGINT          BigInt          bigint
VARCHAR         String           varchar
TIMESTAMP       DateTime         timestamp
BOOLEAN         Boolean          boolean
DECIMAL         Decimal          decimal
```

Your IR should have canonical types:

```text
integer
big_integer
decimal
string
text
boolean
date
datetime
time
json
uuid
binary
```

Then your generator can map:

```text
Schema IR
   ↓
Generator Type System
   ↓
faker/provider
```

This also gives you a place to handle semantic generation.

For example:

```text
email → email generator
phone → phone generator
uuid → UUID generator
created_at → datetime generator
country → country generator
```

---

# 14. Build tests around the IR

For every format, use the **same logical schema**.

For example, create:

```text
fixtures/
  ecommerce.dbml
  ecommerce.sql
  ecommerce.prisma
  ecommerce.py
  ecommerce.json
```

All five describe:

```text
users
products
orders
order_items
```

Then assert:

```python
assert parse(dbml) == expected_schema
assert parse(sql) == expected_schema
assert parse(prisma) == expected_schema
assert parse(sqlalchemy) == expected_schema
assert parse(json) == expected_schema
```

This is much better than writing five completely different test suites.

---

# 15. Recommended implementation phases

### Phase 1 — Foundation

```text
Schema IR
Type system
Relationship model
Constraint model
Validation
Adapter interface
```

### Phase 2 — Existing DBML

```text
DBML → IR
```

Refactor your current implementation.

### Phase 3 — SQL

```text
PostgreSQL
MySQL
SQLite
       ↓
      IR
```

### Phase 4 — Prisma

```text
.prisma
   ↓
Prisma parser
   ↓
IR
```

### Phase 5 — SQLAlchemy

Prefer:

```text
SQLAlchemy MetaData
       ↓
       IR
```

with source parsing as a secondary option.

### Phase 6 — JSON

Implement your own well-defined relational JSON format.

### Phase 7 — UX

Support:

```bash
seed schema.dbml
seed schema.sql
seed schema.prisma
seed models.py
seed schema.json
```

and:

```bash
seed --format postgres --database ...
```

---

# 16. The end state

I'd aim for this:

```text
                    ┌─────────────┐
                    │ DBML        │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ SQL DDL     │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ Prisma      │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ SQLAlchemy  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ JSON        │
                    └──────┬──────┘
                           │
                           ▼
                 ┌───────────────────┐
                 │   Schema IR       │
                 └─────────┬─────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          Validation   Dependency   Semantics
                         Graph
              │            │            │
              └────────────┼────────────┘
                           ▼
                    Data Generator
                           │
                           ▼
                    Constraint Solver
                           │
                           ▼
                     Seed Database
```

### One important correction

If your goal is **maximum adoption**, I would actually rank the formats:

**SQL DDL → Prisma → DBML → SQLAlchemy → JSON/custom schema**

But if your goal is **maximum database coverage**, I'd make the fifth input **database introspection** rather than JSON:

```text
SQL DDL
Prisma
DBML
SQLAlchemy
Live DB introspection
```

That gives you a particularly powerful product because users can either **describe a database** or simply **point your seeder at an existing database and let it discover the schema**.
