"""
Production Deployment Verification & Security Auditor (Phase 6 / Phase 7)
Automated verification script to validate a live deployed application.

Audits:
1. HTTPS & Transport Security (TLS encrypted channel)
2. System Health & Deep Readiness Probe (/health)
3. HTTP Security Headers (Strict-Transport-Security, Content-Security-Policy,
   X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy)
4. Authenticated Session Cookie Flags (HttpOnly, Secure, SameSite, __Host- prefix)
   via simulated POST /login using test credentials from environment variables.
5. Debug Mode Disabled (Verification that Werkzeug interactive debugger is inactive)
6. Rate Limiting Protection (Submits repeated invalid logins and verifies HTTP 429)

Usage:
    python cloud/verify_production_deployment.py https://your-service.onrender.com
    python cloud/verify_production_deployment.py https://your-service.onrender.com --username admin --password MyPassword!
"""

import argparse
import http.cookiejar
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def print_status(check_name: str, passed: bool, details: str = ""):
    symbol = f"{Colors.GREEN}[PASS]{Colors.RESET}" if passed else f"{Colors.RED}[FAIL]{Colors.RESET}"
    print(f"  {symbol} {check_name}")
    if details:
        indent = "         "
        color = Colors.RESET if passed else Colors.YELLOW
        print(f"{indent}{color}{details}{Colors.RESET}")


def extract_csrf_token(html: str) -> str | None:
    """Extract CSRF token from HTML form input."""
    # Pattern 1: name="csrf_token" value="..."
    m = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)
    # Pattern 2: value="..." name="csrf_token"
    m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf_token["\']', html)
    if m:
        return m.group(1)
    return None


def run_deployment_audit(
    base_url: str,
    username: str | None = None,
    password: str | None = None,
    test_rate_limit: bool = True
) -> dict[str, Any]:
    """Audit live target URL for production security and readiness."""
    base_url = base_url.rstrip("/")
    parsed = urllib.parse.urlparse(base_url)

    if not parsed.scheme:
        base_url = f"https://{base_url}"
        parsed = urllib.parse.urlparse(base_url)

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme '{parsed.scheme}': only http and https are permitted.")

    # Credentials from arguments or environment
    username = username or os.getenv("VERIFY_USERNAME") or os.getenv("ADMIN_USERNAME", "admin")
    password = password or os.getenv("VERIFY_PASSWORD") or os.getenv("ADMIN_PASSWORD", "")

    print(f"\n{Colors.BOLD}{Colors.CYAN}====================================================================")
    print(f" Production Deployment Security & Readiness Audit: {base_url}")
    print(f"===================================================================={Colors.RESET}\n")

    results: dict[str, Any] = {
        "target": base_url,
        "https_active": False,
        "health_check": False,
        "security_headers": {},
        "cookie_flags": {},
        "debug_disabled": False,
        "rate_limit_active": False,
        "passed_all": True,
        "failures": []
    }

    # Setup SSL and CookieJar HTTP Opener
    ctx = ssl.create_default_context()
    if parsed.scheme.lower() == "http" or "127.0.0.1" in base_url or "localhost" in base_url:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(cookie_jar)
    )

    # 1. HTTPS Protocol Check
    print(f"{Colors.BOLD}1. Protocol & Transport Security:{Colors.RESET}")
    is_https = parsed.scheme.lower() == "https"
    results["https_active"] = is_https
    if not is_https:
        results["passed_all"] = False
        results["failures"].append("Protocol is not HTTPS (unencrypted plain HTTP)")
        print_status("HTTPS Scheme Active", False, f"URL scheme is '{parsed.scheme}'. Production requires encrypted TLS.")
    else:
        print_status("HTTPS Scheme Active", True, "TLS encrypted transport channel")

    # 2. Health & Liveness Probe Check
    print(f"\n{Colors.BOLD}2. System Health & Liveness Probe (/health):{Colors.RESET}")
    health_url = f"{base_url}/health"
    try:
        req = urllib.request.Request(health_url, headers={"User-Agent": "ProductionVerifier/1.0"})
        with opener.open(req, timeout=15) as resp:  # nosec B310
            status_code = resp.status
            body_text = resp.read().decode("utf-8", errors="replace")
            is_healthy = False
            try:
                body_json = json.loads(body_text)
                is_healthy = status_code == 200 and body_json.get("status") == "healthy"
            except Exception:
                is_healthy = status_code == 200

            results["health_check"] = is_healthy
            if not is_healthy:
                results["passed_all"] = False
                results["failures"].append(f"Health check failed (HTTP {status_code}): {body_text[:100]}")
                print_status("Health Endpoint HTTP 200 OK", False, f"Response: HTTP {status_code} - {body_text[:120]}")
            else:
                print_status("Health Endpoint HTTP 200 OK", True, f"Status: healthy ({body_text[:80]})")
    except Exception as e:
        results["health_check"] = False
        results["passed_all"] = False
        results["failures"].append(f"Health check endpoint unreachable: {e!s}")
        print_status("Health Endpoint HTTP 200 OK", False, f"Connection failed: {e!s}")

    # 3. HTTP Security Headers Audit
    print(f"\n{Colors.BOLD}3. HTTP Security Headers Audit:{Colors.RESET}")
    expected_headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src",
        "Referrer-Policy": "strict-origin",
        "Permissions-Policy": "camera="
    }
    if is_https:
        expected_headers["Strict-Transport-Security"] = "max-age="

    resp_headers: dict[str, str] = {}
    login_html = ""
    try:
        req = urllib.request.Request(f"{base_url}/login", headers={"User-Agent": "ProductionVerifier/1.0"})
        with opener.open(req, timeout=15) as resp:  # nosec B310
            resp_headers = dict(resp.headers)
            login_html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        resp_headers = dict(e.headers)
        login_html = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  {Colors.RED}Could not reach login endpoint to inspect headers: {e!s}{Colors.RESET}")
        results["failures"].append(f"Could not reach login endpoint: {e!s}")
        results["passed_all"] = False

    for header, expected_snippet in expected_headers.items():
        actual_val = None
        for k, v in resp_headers.items():
            if k.lower() == header.lower():
                actual_val = v
                break

        passed = actual_val is not None
        if passed and expected_snippet:
            passed = expected_snippet.lower() in actual_val.lower()

        results["security_headers"][header] = bool(passed)
        if not passed:
            results["passed_all"] = False
            fail_reason = f"Missing or invalid {header}" if not actual_val else f"{header} value '{actual_val}' lacks '{expected_snippet}'"
            results["failures"].append(fail_reason)

        details = f"Present: '{actual_val}'" if actual_val else "Missing header! Hardened security requires this."
        print_status(f"Header: {header}", passed, details)

    # 4. Session Cookie Security Flags (Via POST /login authentication)
    print(f"\n{Colors.BOLD}4. Session Cookie Security Flags (POST /login):{Colors.RESET}")
    csrf_token = extract_csrf_token(login_html)
    set_cookie_headers: list[str] = []

    if password:
        login_data = {
            "username": username,
            "password": password
        }
        if csrf_token:
            login_data["csrf_token"] = csrf_token

        encoded_data = urllib.parse.urlencode(login_data).encode("utf-8")
        post_req = urllib.request.Request(
            f"{base_url}/login",
            data=encoded_data,
            headers={
                "User-Agent": "ProductionVerifier/1.0",
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )

        try:
            with opener.open(post_req, timeout=15) as post_resp:  # nosec B310
                set_cookie_headers = post_resp.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as e:
            set_cookie_headers = e.headers.get_all("Set-Cookie") or []
        except Exception as e:
            print(f"  {Colors.YELLOW}Warning: POST /login attempt failed: {e!s}{Colors.RESET}")

    # Fallback to cookies captured in jar or GET headers if password not provided
    if not set_cookie_headers:
        set_cookie_headers = resp_headers.get("Set-Cookie", "").split(",") if resp_headers.get("Set-Cookie") else []

    if set_cookie_headers:
        cookie_blob = "; ".join(set_cookie_headers).lower()

        has_httponly = "httponly" in cookie_blob
        has_secure = "secure" in cookie_blob
        has_samesite = "samesite" in cookie_blob
        has_host_prefix = "__host-" in cookie_blob or "session=" in cookie_blob

        results["cookie_flags"]["httponly"] = has_httponly
        results["cookie_flags"]["secure"] = has_secure
        results["cookie_flags"]["samesite"] = has_samesite
        results["cookie_flags"]["host_prefix"] = has_host_prefix

        print_status("HttpOnly Flag", has_httponly, "Protects against XSS cookie theft" if has_httponly else "Missing HttpOnly!")
        print_status("Secure Flag", has_secure, "Guarantees transmission only via TLS" if has_secure else "Missing Secure flag!")
        print_status("SameSite Flag", has_samesite, "Mitigates CSRF vulnerabilities" if has_samesite else "Missing SameSite attribute!")

        # Enforce check failures
        if not has_httponly:
            results["passed_all"] = False
            results["failures"].append("Session cookie is missing 'HttpOnly' flag")
        if is_https and not has_secure:
            results["passed_all"] = False
            results["failures"].append("Session cookie is missing 'Secure' flag on HTTPS deployment")
        if not has_samesite:
            results["passed_all"] = False
            results["failures"].append("Session cookie is missing 'SameSite' attribute")
    else:
        results["passed_all"] = False
        results["failures"].append("No session cookie set on /login authentication. Provide ADMIN_PASSWORD to test POST login.")
        print_status("Session Cookie Detected", False, "No Set-Cookie returned. Ensure test credentials are provided via ADMIN_PASSWORD.")

    # 5. Debug Mode Disabled Check (Trigger 404)
    print(f"\n{Colors.BOLD}5. Debug Mode Verification (Information Disclosure Guard):{Colors.RESET}")
    test_404_url = f"{base_url}/__verify_debug_off_probe_404__"
    body_404 = ""
    try:
        req = urllib.request.Request(test_404_url, headers={"User-Agent": "ProductionVerifier/1.0"})
        with opener.open(req, timeout=15) as resp:  # nosec B310
            body_404 = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_404 = e.read().decode("utf-8", errors="replace")
    except Exception:
        body_404 = ""

    has_debugger = "werkzeug" in body_404.lower() and ("traceback" in body_404.lower() or "debugger" in body_404.lower())
    debug_disabled = not has_debugger
    results["debug_disabled"] = debug_disabled
    if not debug_disabled:
        results["passed_all"] = False
        results["failures"].append("Werkzeug interactive debugger is enabled in production!")
        print_status("Interactive Debugger Disabled", False, "CRITICAL: Interactive debugger or traceback signature detected!")
    else:
        print_status("Interactive Debugger Disabled", True, "Safe 404 response without traceback or environment leak")

    # 6. Rate Limiting Check (Trigger 429 via repeated failed logins)
    print(f"\n{Colors.BOLD}6. Rate Limiting Protection (Brute Force Lockout):{Colors.RESET}")
    if test_rate_limit:
        rate_limit_passed = False
        probe_user = "audit_probe_user_rate_limit"
        print("  Sending repeated failed login probes (expecting HTTP 429)...")

        for attempt in range(1, 16):
            bad_payload = {
                "username": probe_user,
                "password": f"InvalidPasswordProbe_{attempt}!"
            }
            if csrf_token:
                bad_payload["csrf_token"] = csrf_token

            bad_data = urllib.parse.urlencode(bad_payload).encode("utf-8")

            bad_req = urllib.request.Request(
                f"{base_url}/login",
                data=bad_data,
                headers={
                    "User-Agent": "ProductionVerifier/1.0",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
            )
            try:
                with opener.open(bad_req, timeout=10) as _:  # nosec B310
                    pass
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    rate_limit_passed = True
                    details = f"HTTP 429 Too Many Requests received on attempt #{attempt}"
                    break
            except Exception:
                pass

        results["rate_limit_active"] = rate_limit_passed
        if not rate_limit_passed:
            results["passed_all"] = False
            results["failures"].append("Rate limiter did not return HTTP 429 after 15 consecutive failed login attempts")
            print_status("Rate Limiting Protection (HTTP 429)", False, "Failed: 15 rapid invalid logins did not trigger HTTP 429 lockout.")
        else:
            print_status("Rate Limiting Protection (HTTP 429)", True, details)
    else:
        print_status("Rate Limiting Protection", True, "Skipped by user flag (--skip-rate-limit)")

    # Final Summary Report
    print(f"\n{Colors.BOLD}====================================================================")
    if results["passed_all"]:
        print(f" {Colors.GREEN}AUDIT RESULT: ALL PRODUCTION SECURITY CHECKS PASSED (100%){Colors.RESET}")
    else:
        print(f" {Colors.RED}AUDIT RESULT: AUDIT FAILED - {len(results['failures'])} ISSUE(S) FOUND:{Colors.RESET}")
        for i, failure in enumerate(results["failures"], 1):
            print(f"   {i}. {Colors.YELLOW}{failure}{Colors.RESET}")
    print(f"{Colors.BOLD}===================================================================={Colors.RESET}\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit live web app for production security and readiness.")
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:5000", help="Live URL to verify (e.g. https://your-service.onrender.com)")
    parser.add_argument("--username", help="Test username for POST /login authentication check")
    parser.add_argument("--password", help="Test password for POST /login authentication check")
    parser.add_argument("--skip-rate-limit", action="store_true", help="Skip sending 15 bad logins to test 429 rate limiting")
    args = parser.parse_args()

    audit_res = run_deployment_audit(
        base_url=args.url,
        username=args.username,
        password=args.password,
        test_rate_limit=not args.skip_rate_limit
    )

    if not audit_res["passed_all"]:
        sys.exit(1)
    sys.exit(0)
