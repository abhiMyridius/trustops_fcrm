"""``resilience.stack``: pointing the drills at a stack, and what stops them."""

from __future__ import annotations

from typing import Any

from resilience.stack import ENV_GATEWAY_URL, ENV_PROJECT, Stack


def test_with_nothing_set_the_drills_target_the_primary_stack() -> None:
    stack = Stack.from_environment({})
    assert stack.docker.project == "trustops"
    assert stack.docker.container("api") == "trustops-api-1"
    assert stack.gateway.base_url == "http://localhost:8080/api/fcrm"


def test_the_compose_project_and_gateway_url_are_taken_from_the_environment() -> None:
    stack = Stack.from_environment(
        {
            ENV_PROJECT: "trustops-res",
            ENV_GATEWAY_URL: "http://localhost:8180/api/fcrm/",
        }
    )
    assert stack.docker.container("harness") == "trustops-res-harness-1"
    assert stack.gateway.base_url == "http://localhost:8180/api/fcrm"


def test_an_empty_variable_means_the_default_not_an_empty_project() -> None:
    stack = Stack.from_environment({ENV_PROJECT: "", ENV_GATEWAY_URL: ""})
    assert stack.docker.project == "trustops"
    assert stack.gateway.base_url == "http://localhost:8080/api/fcrm"


def test_the_variable_names_are_the_ones_the_docs_give() -> None:
    assert (ENV_PROJECT, ENV_GATEWAY_URL) == (
        "FCRM_COMPOSE_PROJECT",
        "FCRM_GATEWAY_URL",
    )


def test_the_mongo_and_docker_handles_follow_the_project(runner: Any) -> None:
    stack = Stack.from_environment({ENV_PROJECT: "other"}, runner=runner)
    runner.reply('[{"_id": "x"}]\n')
    stack.mongo.find("fcrm_harness", "case_runs", {})
    assert runner.calls[0]["argv"][3] == "other-mongo-1"


def test_problems_names_every_container_that_is_missing(
    runner: Any, gateway: Any
) -> None:
    from resilience.errors import CommandError

    stack = Stack.connect(project="nope", runner=runner, gateway=gateway)
    for _ in range(6):
        runner.fail(CommandError("No such object"))
    problems = stack.problems()
    assert len(problems) == 6
    assert problems[0].startswith("nope-mongo-1")
