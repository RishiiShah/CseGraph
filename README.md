# Adaptive Memory-Aware Multi Agent System

This project is an implementation of an "Adaptive Memory-Aware Multi Agent System with Context Sufficiency Estimation for Repository-Level Code Generation".

## Current State: Ingestion Agent

The first phase of the system is the **Ingestion Agent**. The ingestion agent is responsible for walking through a repository's source code and extracting structured semantic elements, such as:
- Classes
- Functions and Methods
- Docstrings and Line numbers
- Import Statements and Dependencies

It uses Python's built-in `ast` (Abstract Syntax Tree) module to safely parse `.py` files without executing them. The extracted structural data is strictly typed using Pydantic models to prepare for the subsequent **Compression** and **Linking Agents**. By organizing and validating the source structure thoroughly, the engine builds an environment ready for graph linkage and LLM augmentation.

## Flow of Development

1. **Ingestion Agent**: Currently parses AST from python repositories into `FileNodes` and `CodeNodes`. 
2. **Compression and Linking Agents** (Upcoming): Will take the outputs of the ingestion agent. A semantic network will link references together, while extractive clustering summarizes code segments dynamically.
3. **Context Sufficiency Estimator (CSE)** (Upcoming): Evaluates if the compiled context from the graph effectively satisfies an LLM generation query.
4. **Code Generation Agent** (Upcoming): Triggers code generation only when the context sufficiency checks pass.

## How to Run the Ingestion Agent

1. Make sure you have the required dependencies installed (mainly `pydantic`):
   ```bash
   pip install pydantic
   ```

2. Navigate directly to the `agents` folder and run the `ingestion_agent.py` script. The script was intentionally designed to be run from inside the `agents` directory without setting custom `PYTHONPATH`s:
   ```bash
   cd agents
   python ingestion_agent.py
   ```
