FROM gitresolve-sandbox:poc
USER root
RUN pip install --no-cache-dir streamlit==1.66.0 markdown-it-py==4.0.0 PyPDF2==3.0.1 "python-dotenv>=1,<2" langchain-core==1.7.3 python-docx==1.1.2
COPY streamlit_gate.py /usr/local/lib/python3.12/site-packages/gitresolve_streamlit_gate.py
COPY streamlit_gate.py /usr/local/lib/gitresolve_streamlit_gate.py
COPY streamlit_pytest.py /usr/local/lib/gitresolve_streamlit_pytest.py
COPY container_entry.py /usr/local/lib/gitresolve_entry.py
USER 10001:10001
