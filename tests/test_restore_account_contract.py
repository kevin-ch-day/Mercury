import pytest

from mercury.restore.account_contract import (
    DEV_SCOPES,
    PRINCIPAL,
    TEMP_RIGHTS,
    TEMP_SCOPE,
    assess_account,
    compact_status,
)


def grants(pi=False):
    rows = [
        "GRANT USAGE ON *.* TO 'mercury_dev_restore'@'localhost' IDENTIFIED BY PASSWORD 'SECRET_VERIFIER'"
    ]
    rows += [
        f"GRANT ALL PRIVILEGES ON `{s}`.* TO 'mercury_dev_restore'@'localhost'"
        for s in DEV_SCOPES
    ]
    rows += [
        f"GRANT {', '.join(sorted(TEMP_RIGHTS))} ON `{TEMP_SCOPE}`.* TO 'mercury_dev_restore'@'localhost'"
    ]
    if pi:
        rows += [
            "GRANT SELECT ON `android_permission_intel`.* TO 'mercury_dev_restore'@'localhost'"
        ]
    return rows


def assess(rows, **kwargs):
    return assess_account(rows, current_user=PRINCIPAL, active_roles=[], **kwargs)


def test_exact_and_temporary_read_window():
    assert assess(grants())["status"] == "EXACT"
    assert assess(grants(True), pi_read_window=True)["status"] == "EXACT"
    assert assess(grants(), pi_read_window=True)["status"] == "MISSING"
    assert assess(grants(True))["status"] == "BROADER"
    assert "SECRET_VERIFIER" not in str(assess(grants()))


def test_compact_status_omits_expected_rights_inventory():
    result = assess(grants())

    output = compact_status(result)

    assert "Restore account :: EXACT" in output
    assert "PI read window  :: inactive" in output
    assert "Missing scopes  :: 0" in output
    assert "CREATE ROUTINE" not in output


def test_old_contract_missing_routines():
    rows = [
        r.replace("ALTER ROUTINE, ", "").replace("CREATE ROUTINE, ", "")
        for r in grants()
    ]
    assert assess(rows)["missing"][TEMP_SCOPE] == ["ALTER ROUTINE", "CREATE ROUTINE"]


@pytest.mark.parametrize(
    "extra",
    [
        "GRANT ALL PRIVILEGES ON *.* TO x",
        "GRANT SELECT ON `android_permission_intel`.* TO x WITH GRANT OPTION",
        "GRANT INSERT ON `android_permission_intel`.* TO x",
        "GRANT CREATE ROUTINE ON `erebus_threat_intel_prod`.* TO x",
        r"GRANT ALTER ROUTINE ON `\_restorecheck\_one`.* TO x",
        "GRANT EXECUTE ON PROCEDURE `x`.`p` TO x",
        "GRANT admin_role TO x",
        "GRANT PROXY ON x TO y",
    ],
)
def test_excess_and_unrecognized_authority_fail_closed(extra):
    assert assess(grants(True) + [extra], pi_read_window=True)["status"] == "BROADER"


def test_roles_identity_and_repeated_status():
    rows = grants()
    assert assess(rows) == assess(rows)
    assert (
        assess_account(rows, current_user="root@localhost", active_roles=[])["status"]
        == "BROADER"
    )
    assert (
        assess_account(rows, current_user=PRINCIPAL, active_roles=["role"])["status"]
        == "BROADER"
    )


def test_disposable_automatic_routine_grants_are_bounded():
    for name in ["_restorecheck_success", "erebus_threat_intel_dev"]:
        assert (
            assess(
                grants()
                + [f"GRANT EXECUTE, ALTER ROUTINE ON PROCEDURE `{name}`.`p` TO x"]
            )["status"]
            == "EXACT"
        )
    for name in ["android_permission_intel", "erebus_threat_intel_prod"]:
        assert (
            assess(
                grants(True) + [f"GRANT EXECUTE ON PROCEDURE `{name}`.`p` TO x"],
                pi_read_window=True,
            )["status"]
            == "BROADER"
        )


def test_status_error_does_not_leak_connector_secrets(monkeypatch, capsys):
    from mercury.restore.account_contract import main

    monkeypatch.setattr("sys.argv", ["account-contract"])

    def fail():
        raise RuntimeError("SECRET_VERIFIER password=do-not-print")

    monkeypatch.setattr(
        "mercury.database.mariadb.config.load_mariadb_restore_config", fail
    )
    assert main() == 2
    output = capsys.readouterr().out
    assert "INSPECTION_FAILED" in output
    assert "SECRET_VERIFIER" not in output and "do-not-print" not in output


def test_help_never_loads_private_configuration(monkeypatch, capsys):
    from mercury.restore.account_contract import main

    monkeypatch.setattr("sys.argv", ["account-contract", "--help"])

    def fail():
        raise AssertionError("help must not connect or load secrets")

    monkeypatch.setattr(
        "mercury.database.mariadb.config.load_mariadb_restore_config", fail
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--pi-read-window" in output
    assert "--compact" in output
