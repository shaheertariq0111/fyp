from src.repositories.customer_repository import CustomerRepository


class FakeCustomerTable:
    def __init__(self):
        self.scan_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        return {
            "Items": [{
                "PK": "CUSTOMER#cust-2",
                "SK": "PROFILE",
                "customer_id": "cust-2",
            }],
            "LastEvaluatedKey": {
                "PK": "CUSTOMER#cust-2",
                "SK": "PROFILE",
            },
        }


class FakeDynamoDb:
    def __init__(self, table):
        self.table = table

    def Table(self, table_name):
        assert table_name == "customers-test"
        return self.table


def test_customer_list_page_uses_one_bounded_scan_and_continues_from_key():
    table = FakeCustomerTable()
    repository = CustomerRepository(FakeDynamoDb(table), "customers-test")
    start_key = {"PK": "CUSTOMER#cust-1", "SK": "PROFILE"}

    result = repository.list_page(limit=25, exclusive_start_key=start_key)

    assert table.scan_calls == [{
        "Limit": 25,
        "ExclusiveStartKey": start_key,
    }]
    assert result == {
        "items": [{
            "PK": "CUSTOMER#cust-2",
            "SK": "PROFILE",
            "customer_id": "cust-2",
        }],
        "last_evaluated_key": {
            "PK": "CUSTOMER#cust-2",
            "SK": "PROFILE",
        },
    }


def test_customer_list_page_rejects_unbounded_or_invalid_limits():
    repository = CustomerRepository(FakeDynamoDb(FakeCustomerTable()), "customers-test")

    for limit in (True, 0, 101):
        try:
            repository.list_page(limit=limit)
        except ValueError as exc:
            assert str(exc) == "customer page limit must be between 1 and 100"
        else:
            raise AssertionError("invalid customer page limit was accepted")
