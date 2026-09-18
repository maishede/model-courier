# Security policy

ModelCourier handles bearer tokens and user-uploaded media. Do not disclose a
token, private input, or a reproducible exploit in a public issue.

Please report suspected vulnerabilities through a private GitHub security
advisory for [maishede/model-courier](https://github.com/maishede/model-courier/security/advisories/new).
Include the affected commit, reproduction steps, impact, and a safe contact
method. The maintainer will acknowledge reports and coordinate a fix before
public disclosure when practical.

Deployments should use HTTPS, rotate device and Worker tokens, keep the data
directory private, and review every optional Provider dependency and model
license before installation.
