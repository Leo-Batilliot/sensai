import pytest

from sensai.mcp_servers.sql_toolbox.target import Target, simple_target


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "DELETE FROM clients WHERE status = 'inactive'",
            Target("DELETE", "clients", "clients", "status = 'inactive'"),
        ),
        (
            "-- purge\nDELETE FROM clients WHERE id NOT IN (SELECT client_id FROM orders);",
            Target("DELETE", "clients", "clients", "id NOT IN (SELECT client_id FROM orders)"),
        ),
        (
            "delete from \"clients\" where name = 'O''Neil (WHERE)'",
            Target("DELETE", "clients", '"clients"', "name = 'O''Neil (WHERE)'"),
        ),
        ("DELETE FROM clients", Target("DELETE", "clients", "clients", "")),
        (
            "UPDATE clients SET status = 'archived', name = upper(name) WHERE id IN (SELECT 1)",
            Target("UPDATE", "clients", "clients", "id IN (SELECT 1)"),
        ),
    ],
)
def test_plain_forms(sql: str, expected: Target) -> None:
    assert simple_target(sql) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE clients SET status = 'x' FROM other WHERE clients.id = other.id",
        "DELETE FROM clients AS c WHERE c.id = 1",
        "DELETE FROM clients WHERE id = 1 RETURNING id",
        "WITH x AS (SELECT 1) DELETE FROM clients WHERE id IN x",
        "INSERT INTO clients VALUES (1)",
        "UPDATE OR REPLACE clients SET a = 1",
        "DELETE FROM clients WHERE",
        "DELETE FROM main.clients WHERE id = 1",
    ],
)
def test_other_forms_are_not_parsed(sql: str) -> None:
    assert simple_target(sql) is None


def test_select_on_the_target() -> None:
    target = Target("DELETE", "clients", "clients", "a = 1 OR b = 2")
    assert target.select("COUNT(*)", "c = 3") == (
        "SELECT COUNT(*) FROM clients WHERE (\na = 1 OR b = 2\n) AND c = 3"
    )
    assert Target("DELETE", "t", "t", "").select() == "SELECT * FROM t"
