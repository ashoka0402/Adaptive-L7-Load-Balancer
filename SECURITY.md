# Security policy

Pulse is an experimental local infrastructure tool. Do not expose the control plane to the public internet. Use a unique external admin token, trusted DNS, network egress restrictions and TLS termination for any nonlocal use. Backend registration is restricted to startup-configured destinations. For details see [release security boundaries](docs/release.md).

Report suspected vulnerabilities privately using GitHub's private vulnerability reporting if enabled, or contact the repository owner through a private channel. Do not disclose credentials, target real systems, or perform destructive testing. Include the affected commit, reproduction on isolated infrastructure, expected and actual behavior, and possible mitigation. No response-time or supported-version guarantee is made.
