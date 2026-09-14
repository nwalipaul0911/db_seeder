from seeder_engine.prisma_adapter import PrismaAdapter
from seeder_engine.schema_loader import load_schema


PRISMA = """
enum Status {
  ACTIVE
  INACTIVE
}

model User {
  id        Int      @id @default(autoincrement())
  email     String   @unique
  status    Status   @default(ACTIVE)
  orders    Order[]
  createdAt DateTime @default(now())
}

model Order {
  id     Int  @id @default(autoincrement())
  userId Int
  user   User @relation(fields: [userId], references: [id])
  total  Decimal

  @@index([userId])
}
"""


def test_prisma_adapter_builds_schema_ir():
    schema = PrismaAdapter().parse(PRISMA)

    assert schema.metadata["source_format"] == "prisma"
    assert schema.enum_map["Status"].values == ("ACTIVE", "INACTIVE")
    assert schema.table_map["User"].primary_key == ("id",)
    assert schema.table_map["User"].columns[2].data_type.enum_name == "Status"
    assert schema.table_map["User"].columns[1].unique is True
    assert schema.table_map["Order"].columns[1].references.target_table == "User"
    assert schema.table_map["Order"].indexes[0].columns == ("userId",)


def test_prisma_auto_detection_and_loader():
    assert PrismaAdapter().detect(PRISMA)
    schema = load_schema(PRISMA)
    assert schema.table_map["Order"].columns[2].data_type.name == "decimal"


def test_prisma_adapter_parses_multiline_relations():
    schema = PrismaAdapter().parse(
        """
        model User {
          id String @id
          addresses Address[]
        }

        model Address {
          id String @id
          userId String
          user User @relation(
            fields: [userId],
            references: [id]
          )
        }
        """
    )

    assert len(schema.relationships) == 1
    assert schema.relationships[0].source_table == "Address"
    assert schema.relationships[0].source_columns == ("userId",)
    assert schema.relationships[0].target_table == "User"
    assert schema.relationships[0].target_columns == ("id",)


def test_prisma_adapter_parses_composite_constraints():
    schema = PrismaAdapter().parse(
        """
        model OrderItem {
          orderId String
          productId String
          order Order @relation(fields: [orderId], references: [id])
          product Product @relation(fields: [productId], references: [id])

          @@id([orderId, productId])
        }

        model Order {
          id String @id
          items OrderItem[]
        }

        model Product {
          id String @id
          items OrderItem[]
        }
        """
    )

    assert schema.table_map["OrderItem"].primary_key == ("orderId", "productId")
    assert schema.table_map["OrderItem"].primary_key[0].startswith("[") is False
