from app.fingerprint import attach_network_profile, failed_network_profile, normalize_network_probe


def test_normalize_ip_api_success_sets_timezone_asn_and_dns():
    network = normalize_network_probe(
        "ip-api.com",
        {
            "status": "success",
            "query": "18.142.142.128",
            "country": "Singapore",
            "countryCode": "SG",
            "regionName": "Central Singapore",
            "city": "Singapore",
            "zip": "048582",
            "lat": 1.28009,
            "lon": 103.851,
            "timezone": "Asia/Singapore",
            "isp": "Amazon Technologies Inc.",
            "as": "AS16509 Amazon.com, Inc.",
        },
    )

    assert network is not None
    assert network["ip"] == "18.142.142.128"
    assert network["timezone"] == "Asia/Singapore"
    assert network["asn"] == "AS16509 Amazon.com, Inc."
    assert network["dnsServers"] == ["1.1.1.1", "8.8.8.8"]


def test_normalize_ip234_converts_string_coordinates_and_cn_dns():
    network = normalize_network_probe(
        "ip234.in",
        {
            "ip": "115.198.100.117",
            "city": "Hangzhou",
            "organization": "Chinanet",
            "asn": 4134,
            "country": "China",
            "country_code": "CN",
            "postal": "310000",
            "latitude": "30.2674",
            "longitude": "120.171",
            "timezone": "Asia/Shanghai",
            "region": "Zhejiang",
        },
    )

    assert network is not None
    assert network["lat"] == 30.2674
    assert network["lon"] == 120.171
    assert network["asn"] == "AS4134"
    assert network["dnsServers"] == ["223.5.5.5", "119.29.29.29"]


def test_failed_network_profile_keeps_warning_and_utc_fallback_visible():
    network = failed_network_profile("all probes failed", ["ip-api.com probe failed"])

    assert network["source"] == "unresolved"
    assert network["timezone"] == "UTC"
    assert "ip-api.com probe failed" in network["warnings"]
    assert "all probes failed" in network["warnings"]


def test_attach_network_profile_binds_timezone_and_network():
    profile = {"timezone": "UTC", "navigator": {"languages": ["zh-CN", "zh", "en"]}}
    network = normalize_network_probe(
        "ip-api.com",
        {"status": "success", "query": "1.1.1.1", "countryCode": "AU", "timezone": "Australia/Sydney"},
    )

    attach_network_profile(profile, network)

    assert profile["timezone"] == "Australia/Sydney"
    assert profile["network"]["timezone"] == "Australia/Sydney"
    assert profile["navigator"]["languages"] == ["zh-CN", "zh", "en"]
