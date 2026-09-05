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
