# The sealed image audit checks run in (app/audit/tools.py run_check). Built by WhiteHats ahead of time; the audited
# code never installs anything. Common tools for small Python checks and for reading Terraform and YAML locally.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN pip install --no-cache-dir pytest==8.4.2 pyyaml==6.0.3 python-hcl2==7.3.1 boto3==1.40.70 moto==5.1.16 \
    && useradd --uid 65534 --no-create-home --shell /usr/sbin/nologin sandbox 2>/dev/null || true
USER 65534:65534
