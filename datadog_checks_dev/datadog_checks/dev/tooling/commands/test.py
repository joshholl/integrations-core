# (C) Datadog, Inc. 2018-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
import os
import sys

import click

from ..._env import DDTRACE_OPTIONS_LIST, E2E_PARENT_PYTHON, SKIP_ENVIRONMENT
from ...ci import get_ci_env_vars, running_on_ci
from ...fs import chdir, file_exists, remove_path
from ...subprocess import run_command
from ...utils import ON_WINDOWS, get_next
from ..constants import get_root
from ..dependencies import read_check_base_dependencies
from ..testing import construct_pytest_options, fix_coverage_report, get_tox_envs, pytest_coverage_sources
from ..utils import code_coverage_enabled, complete_testable_checks
from .console import CONTEXT_SETTINGS, abort, echo_debug, echo_info, echo_success, echo_waiting, echo_warning


def display_envs(check_envs):
    for check, envs in check_envs:
        echo_success(f'`{check}`:')
        for e in envs:
            echo_info(f'    {e}')


@click.command(context_settings=CONTEXT_SETTINGS, short_help='Run tests')
@click.argument('checks', autocompletion=complete_testable_checks, nargs=-1)
@click.option('--format-style', '-fs', is_flag=True, help='Run only the code style formatter')
@click.option('--style', '-s', is_flag=True, help='Run only style checks')
@click.option('--bench', '-b', is_flag=True, help='Run only benchmarks')
@click.option('--latest-metrics', is_flag=True, help='Only verify support of new metrics')
@click.option('--e2e', is_flag=True, help='Run only end-to-end tests')
@click.option('--ddtrace', is_flag=True, help='Run tests using dd-trace-py')
@click.option('--cov', '-c', 'coverage', is_flag=True, help='Measure code coverage')
@click.option('--cov-missing', '-cm', is_flag=True, help='Show line numbers of statements that were not executed')
@click.option('--junit', '-j', 'junit', is_flag=True, help='Generate junit reports')
@click.option('--marker', '-m', help='Only run tests matching given marker expression')
@click.option('--filter', '-k', 'test_filter', help='Only run tests matching given substring expression')
@click.option('--pdb', 'enter_pdb', is_flag=True, help='Drop to PDB on first failure, then end test session')
@click.option('--debug', '-d', is_flag=True, help='Set the log level to debug')
@click.option('--verbose', '-v', count=True, help='Increase verbosity (can be used additively)')
@click.option('--list', '-l', 'list_envs', is_flag=True, help='List available test environments')
@click.option('--passenv', help='Additional environment variables to pass down')
@click.option('--changed', is_flag=True, help='Only test changed checks')
@click.option('--cov-keep', is_flag=True, help='Keep coverage reports')
@click.option('--skip-env', is_flag=True, help='Skip environment creation and assume it is already running')
@click.option('--pytest-args', '-pa', help='Additional arguments to pytest')
@click.option('--force-base-unpinned', is_flag=True, help='Force using datadog-checks-base as specified by check dep')
@click.option('--force-base-min', is_flag=True, help='Force using lowest viable release version of datadog-checks-base')
@click.option('--force-env-rebuild', is_flag=True, help='Force creating a new env')
@click.pass_context
def test(
    ctx,
    checks,
    format_style,
    style,
    bench,
    latest_metrics,
    e2e,
    ddtrace,
    coverage,
    junit,
    cov_missing,
    marker,
    test_filter,
    enter_pdb,
    debug,
    verbose,
    list_envs,
    passenv,
    changed,
    cov_keep,
    skip_env,
    pytest_args,
    force_base_unpinned,
    force_base_min,
    force_env_rebuild,
):
    """Run tests for Agent-based checks.

    If no checks are specified, this will only test checks that
    were changed compared to the master branch.

    You can also select specific comma-separated environments to test like so:

    \b
    `$ ddev test mysql:mysql57,maria10130`
    """
    if list_envs:
        check_envs = get_tox_envs(checks, every=True, sort=True, changed_only=changed)
        display_envs(check_envs)
        return

    root = get_root()
    testing_on_ci = running_on_ci()
    color = ctx.obj['color']

    # Implicitly track coverage
    if cov_missing:
        coverage = True

    if e2e:
        marker = 'e2e'

    coverage_show_missing_lines = str(cov_missing or testing_on_ci)

    test_env_vars = {
        # Environment variables we need tox to pass down
        'TOX_TESTENV_PASSENV': (
            # Used in .coveragerc for whether or not to show missing line numbers for coverage
            # or for generic tag checking
            'DDEV_* '
            # Necessary for compilation on Windows: PROGRAMDATA, PROGRAMFILES, PROGRAMFILES(X86)
            'PROGRAM* '
            # Necessary for getting the user on Windows https://docs.python.org/3/library/getpass.html#getpass.getuser
            'USERNAME '
            # Space-separated list of pytest options
            'PYTEST_ADDOPTS '
            # https://docs.docker.com/compose/reference/envvars/
            'DOCKER_* COMPOSE_*'
        ),
        'DDEV_COV_MISSING': coverage_show_missing_lines,
    }

    if skip_env:
        test_env_vars[SKIP_ENVIRONMENT] = 'true'
        test_env_vars['TOX_TESTENV_PASSENV'] += f' {SKIP_ENVIRONMENT}'

    if passenv:
        test_env_vars['TOX_TESTENV_PASSENV'] += f' {passenv}'

    test_env_vars['TOX_TESTENV_PASSENV'] += f" {' '.join(get_ci_env_vars())}"

    if color is not None:
        test_env_vars['PY_COLORS'] = '1' if color else '0'

    if e2e:
        test_env_vars[E2E_PARENT_PYTHON] = sys.executable
        test_env_vars['TOX_TESTENV_PASSENV'] += f' {E2E_PARENT_PYTHON}'

    if ddtrace:
        for env in DDTRACE_OPTIONS_LIST:
            test_env_vars['TOX_TESTENV_PASSENV'] += f' {env}'
        # Used for CI app product
        test_env_vars['TOX_TESTENV_PASSENV'] += ' TF_BUILD BUILD* SYSTEM*'
        test_env_vars['DD_SERVICE'] = os.getenv('DD_SERVICE', 'ddev-integrations')

    org_name = ctx.obj['org']
    org = ctx.obj['orgs'].get(org_name, {})
    api_key = org.get('api_key') or ctx.obj['dd_api_key'] or os.getenv('DD_API_KEY')
    if api_key:
        test_env_vars['DD_API_KEY'] = api_key
        test_env_vars['TOX_TESTENV_PASSENV'] += ' DD_API_KEY'

    check_envs = get_tox_envs(checks, style=style, format_style=format_style, benchmark=bench, changed_only=changed)
    tests_ran = False

    for check, envs in check_envs:
        # Many checks don't have benchmark envs, etc.
        if not envs:
            echo_debug(f"No envs found for: `{check}`")
            continue

        ddtrace_check = ddtrace
        if ddtrace and ON_WINDOWS and any('py2' in env for env in envs):
            # The pytest flag --ddtrace is not available for windows-py2 env.
            # Removing it so it does not fail.
            echo_warning(
                'ddtrace flag is not available for windows-py2 environments ; disabling the flag for this check.'
            )
            ddtrace_check = False

        # This is for ensuring proper spacing between output of multiple checks' tests.
        # Basically this avoids printing a new line before the first check's tests.
        output_separator = '\n' if tests_ran else ''

        # For performance reasons we're generating what to test on the fly and therefore
        # need a way to tell if anything ran since we don't know anything upfront.
        tests_ran = True

        # Build pytest options
        pytest_options = construct_pytest_options(
            check=check,
            verbose=verbose,
            color=color,
            enter_pdb=enter_pdb,
            debug=debug,
            bench=bench,
            latest_metrics=latest_metrics,
            coverage=coverage,
            junit=junit,
            marker=marker,
            test_filter=test_filter,
            pytest_args=pytest_args,
            e2e=e2e,
            ddtrace=ddtrace_check,
        )
        if coverage:
            pytest_options = pytest_options.format(pytest_coverage_sources(check))
        test_env_vars['PYTEST_ADDOPTS'] = pytest_options

        if verbose:
            echo_info(f"pytest options: `{test_env_vars['PYTEST_ADDOPTS']}`")

        with chdir(os.path.join(root, check), env_vars=test_env_vars):
            if format_style:
                test_type_display = 'the code formatter'
            elif style:
                test_type_display = 'only style checks'
            elif bench:
                test_type_display = 'only benchmarks'
            elif latest_metrics:
                test_type_display = 'only latest metrics validation'
            elif e2e:
                test_type_display = 'only end-to-end tests'
            else:
                test_type_display = 'tests'

            wait_text = f'{output_separator}Running {test_type_display} for `{check}`'
            echo_waiting(wait_text)
            echo_waiting('-' * len(wait_text))

            command = [
                'tox',
                # so users won't get failures for our possibly strict CI requirements
                '--skip-missing-interpreters',
                # so coverage tracks the real locations instead of .tox virtual envs
                '--develop',
                # comma-separated list of environments
                '-e {}'.format(','.join(envs)),
            ]

            env = os.environ.copy()

            base_or_dev = check.startswith('datadog_checks_')
            if force_base_min and not base_or_dev:
                check_base_dependencies, errors = read_check_base_dependencies(check)
                if errors:
                    abort(f'\nError collecting base package dependencies: {errors}')

                spec_set = list(check_base_dependencies['datadog-checks-base'].keys())[0]

                spec = get_next(spec_set) if spec_set else None
                if spec is None or spec.operator != '>=':
                    abort(f'\nFailed to determine minimum version of package `datadog_checks_base`: {spec}')

                version = spec.version
                env['TOX_FORCE_INSTALL'] = f"datadog_checks_base[deps]=={version}"
            elif force_base_unpinned and not base_or_dev:
                env['TOX_FORCE_UNPINNED'] = "datadog_checks_base"
            elif (force_base_min or force_base_unpinned) and base_or_dev:
                echo_info(f'Skipping forcing base dependency for check {check}')

            if force_env_rebuild:
                command.append('--recreate')

            if verbose:
                command.append('-' + 'v' * verbose)

            command = ' '.join(command)

            echo_debug(f'TOX COMMAND: {command}')
            result = run_command(command, env=env)

            if result.code:
                abort('\nFailed!', code=result.code)

            if coverage and file_exists('.coverage') and code_coverage_enabled(check):
                if not cov_keep:
                    echo_info('\n---------- Coverage report ----------\n')

                    result = run_command('coverage report --rcfile=../.coveragerc')
                    if result.code:
                        abort('\nFailed!', code=result.code)

                if testing_on_ci:
                    result = run_command('coverage xml -i --rcfile=../.coveragerc')
                    if result.code:
                        abort('\nFailed!', code=result.code)

                    fix_coverage_report(check, 'coverage.xml')
                    run_command(['codecov', '-X', 'gcov', '--root', root, '-F', check, '-f', 'coverage.xml'])
                else:
                    if not cov_keep:
                        remove_path('.coverage')
                        remove_path('coverage.xml')

        echo_success('\nPassed!')

        # You can only test one environment at a time since the setup/tear down occurs elsewhere
        if e2e:
            break

    if not tests_ran:
        if format_style:
            echo_warning('Code formatting is not enabled!')
            echo_info('To enable it, set `dd_check_style = true` under the `[testenv]` section of `tox.ini`.')
        else:
            echo_info('Nothing to test!')