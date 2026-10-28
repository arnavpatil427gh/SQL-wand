from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db
from . import sql_tools

app = FastAPI(title="SQL Assistant API")


class QueryRequest(BaseModel):
    sql: str


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/schema")
def schema():
    return {"tables": db.get_schema()}


@app.post("/api/query")
def query(req: QueryRequest):
    if not req.sql.strip():
        return JSONResponse(status_code=400, content={"error": {"message": "Query is empty"}})
    try:
        return jsonable_encoder(db.run_query(req.sql))
    except db.QueryError as e:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": e.message, "position": e.position, "hint": e.hint}},
        )


@app.post("/api/analyze")
def analyze(req: QueryRequest):
    """Return syntax validity, error locations, and referenced tables/columns."""
    errs = sql_tools.validate(req.sql)
    info = sql_tools.extract_tables_and_columns(req.sql)
    return {
        "valid": len(errs) == 0,
        "errors": errs,
        "tables": info["tables"],
        "columns": info["columns"],
    }


@app.post("/api/format")
def format_query(req: QueryRequest):
    """Return pretty-printed SQL."""
    return {"sql": sql_tools.format_sql(req.sql)}

