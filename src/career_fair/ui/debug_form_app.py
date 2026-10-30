import streamlit as st

st.set_page_config(page_title="Form diagnostic")

st.title("Form diagnostic")

with st.form("login_form"):
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    submitted = st.form_submit_button("Sign in")

st.write("Form rendered successfully")