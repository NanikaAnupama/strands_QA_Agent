"""AWS deployment shim for the QA agent.

Nothing in here is imported by the local CLI / Starlette / Streamlit paths — it
exists purely so the same pipeline can run as two Lambda functions behind a
CloudFront distribution. See deploy/README.md.
"""
