"""Guards: small pure checks the loop runs in code before it trusts a URL or touches a site. `robots` decides whether a
page may be fetched (RFC 9309, failing closed); `hosts` says whether a posting's host is a known job portal, an official
applicant-tracking system, or unverified. Neither module touches the network: callers pass in what they need."""
