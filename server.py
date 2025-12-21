from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from typing import List, Optional
import uvicorn
import os
import sys
import requests
import hashlib
import json
import time

app = FastAPI()

MAPS_DIR = os.path.join("static", "maps")
os.makedirs(MAPS_DIR, exist_ok=True)

# --- Wikidata Logic (Ported from manage_datasets.py) ---

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
API_ENDPOINT = "https://www.wikidata.org/w/api.php"
HEADERS = {
    "User-Agent": "PinpointGame/1.1 (https://github.com/pinpoint.linush.org; linush@example.com)"
}

class Constraint(BaseModel):
    property: str
    value: str

class DatasetConfig(BaseModel):
    item_type: str
    constraints: List[Constraint]
    dataset_type: str  # 'point' or 'polygon'
    min_pop: int = 0
    exclude_dissolved: bool = True
    limit: int = 100

class SaveRequest(BaseModel):
    id: Optional[str] = None
    config: DatasetConfig
    name: str
    description: str
    prompt_template: str
    sub_prompt_template: Optional[str] = ""

class DeleteRequest(BaseModel):
    id: str

def search_wikidata(query, type_filter='item'):
    try:
        params = {
            'action': 'wbsearchentities',
            'format': 'json',
            'language': 'en',
            'type': type_filter,
            'search': query,
            'limit': 10
        }
        r = requests.get(API_ENDPOINT, params=params, headers=HEADERS)
        data = r.json()
        return data.get('search', [])
    except Exception as e:
        print(f"Search failed: {e}")
        return []

def build_query(config: DatasetConfig):
    sparql = "SELECT DISTINCT ?item ?itemLabel "
    
    if config.dataset_type == 'point':
        sparql += "?coord "
    elif config.dataset_type == 'polygon':
        sparql += "?geoShapeUrl "
        
    sparql += "?sitelinks "
    sparql += "WHERE {\n"
    
    # Main instance type
    sparql += f"  ?item wdt:P31/wdt:P279* wd:{config.item_type}.\n"
    
    # Constraints
    for c in config.constraints:
        prop = c.property
        val = c.value
        is_negative = prop.startswith('-')
        if is_negative:
            prop = prop[1:]
            sparql += f"  FILTER NOT EXISTS {{ ?item wdt:{prop} wd:{val}. }}\n"
        else:
            sparql += f"  ?item wdt:{prop} wd:{val}.\n"
    
    # FILTER: Population
    if config.min_pop > 0:
        sparql += f"  ?item wdt:P1082 ?pop. FILTER(?pop >= {config.min_pop}).\n"

    # FILTER: Exclude Dissolved
    if config.exclude_dissolved:
        sparql += "  FILTER NOT EXISTS { ?item wdt:P576 ?end. }\n"
        
    # Type specific properties
    if config.dataset_type == 'point':
        sparql += "  ?item wdt:P625 ?coord.\n"
    elif config.dataset_type == 'polygon':
        sparql += "  ?item wdt:P3896 ?geoShapeUrl.\n"
        
    # Sitelinks for sorting relevance
    sparql += "  ?item wikibase:sitelinks ?sitelinks.\n"

    # Label service
    sparql += '  SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }\n'
    sparql += "}\n"
    
    # ORDER BY Relevance (Sitelinks)
    sparql += "ORDER BY DESC(?sitelinks)\n"
    sparql += f"LIMIT {config.limit}"
    
    return sparql

def fetch_sparql(query):
    try:
        response = requests.get(
            SPARQL_ENDPOINT,
            params={'format': 'json', 'query': query},
            headers=HEADERS
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

def parse_results(data, dataset_type):
    results = []
    bindings = data['results']['bindings']
    
    for item in bindings:
        try:
            entry = {
                "label": item['itemLabel']['value'],
                "id": item['item']['value'].split('/')[-1]
            }
            
            if dataset_type == 'point':
                wkt = item['coord']['value']
                clean = wkt.replace("Point(", "").replace(")", "")
                lon, lat = map(float, clean.split())
                entry['lat'] = lat
                entry['lng'] = lon
            elif dataset_type == 'polygon':
                entry['geoShapeUrl'] = item['geoShapeUrl']['value']
                
            results.append(entry)
        except Exception as e:
            continue
            
    return results

# --- API Endpoints ---

@app.get("/api/search")
async def api_search(q: str, type: str = "item"):
    results = search_wikidata(q, type)
    return results

@app.post("/api/preview")
async def api_preview(config: DatasetConfig):
    query = build_query(config)
    raw_data = fetch_sparql(query)
    if not raw_data:
        raise HTTPException(status_code=500, detail="Failed to fetch data from Wikidata")
    
    parsed = parse_results(raw_data, config.dataset_type)
    return parsed

@app.post("/api/save")
async def api_save(req: SaveRequest):
    # 1. Fetch Data (to ensure we have the latest)
    query = build_query(req.config)
    raw_data = fetch_sparql(query)
    if not raw_data:
        raise HTTPException(status_code=500, detail="Failed to fetch data")
        
    parsed_data = parse_results(raw_data, req.config.dataset_type)
    
    if not parsed_data:
        raise HTTPException(status_code=400, detail="No items found to save")

    # Load existing datasets
    datasets = []
    if os.path.exists("datasets.json"):
        with open("datasets.json", "r") as f:
            try:
                datasets = json.load(f)
            except:
                pass

    # Determine ID and Filename
    if req.id:
        # Update existing
        dataset_id = req.id
        existing_entry = next((d for d in datasets if d['id'] == dataset_id), None)
        if not existing_entry:
             raise HTTPException(status_code=404, detail="Dataset not found")
        filename = existing_entry['filename']
    else:
        # Create new
        dataset_id = f"custom_{int(time.time())}"
        filename = f"dataset_{int(time.time())}.json"

    # 2. Save Data File
    filepath = os.path.join("static", filename)
    
    with open(filepath, "w") as f:
        json.dump(parsed_data, f, indent=2)
        
    # 3. Update datasets.json
    new_entry = {
        "id": dataset_id,
        "name": req.name,
        "description": req.description,
        "type": req.config.dataset_type,
        "filename": filename,
        "prompt_template": req.prompt_template,
        "sub_prompt_template": req.sub_prompt_template,
        "config": req.config.dict(),
        "data_keys": { "label": "label" }
    }
    
    if req.config.dataset_type == 'point':
        new_entry['data_keys'].update({'lat': 'lat', 'lng': 'lng'})
    else:
        new_entry['data_keys']['geoShapeUrl'] = 'geoShapeUrl'
        
    if req.id:
        # Replace existing
        for i, d in enumerate(datasets):
            if d['id'] == req.id:
                datasets[i] = new_entry
                break
    else:
        datasets.append(new_entry)
    
    with open("datasets.json", "w") as f:
        json.dump(datasets, f, indent=2)
        
    print(f"Saved dataset {dataset_id} to datasets.json. Total count: {len(datasets)}")
    return {"message": "Dataset saved successfully", "id": dataset_id}

@app.post("/api/delete")
async def api_delete(req: DeleteRequest):
    if not os.path.exists("datasets.json"):
        raise HTTPException(status_code=404, detail="No datasets found")
    
    with open("datasets.json", "r") as f:
        datasets = json.load(f)
    
    target = next((d for d in datasets if d['id'] == req.id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Dataset not found")
    
    # Delete data file
    filepath = os.path.join("static", target['filename'])
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            print(f"Error deleting file {filepath}: {e}")
        
    # Remove from list
    datasets = [d for d in datasets if d['id'] != req.id]
    
    with open("datasets.json", "w") as f:
        json.dump(datasets, f, indent=2)
        
    return {"message": "Dataset deleted"}

@app.get("/datasets")
async def get_datasets():
    if os.path.exists("datasets.json"):
        try:
            with open("datasets.json", "r") as f:
                data = json.load(f)
            return JSONResponse(content=data, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
        except Exception as e:
            print(f"Error reading datasets.json: {e}")
            return JSONResponse(status_code=500, content={"message": "Error reading datasets"})
    return JSONResponse(status_code=404, content={"message": "Datasets not found"})

@app.get("/proxy_map")
async def proxy_map(url: str):
    url = url.replace("+", " ")
    # Create a safe filename from the URL
    filename = hashlib.md5(url.encode()).hexdigest() + ".json"
    file_path = os.path.join(MAPS_DIR, filename)

    if os.path.exists(file_path):
        return FileResponse(file_path)
    
    try:
        resp = requests.get(url, headers={"User-Agent": "PinpointGame/1.0"})
        resp.raise_for_status()
        
        with open(file_path, "wb") as f:
            f.write(resp.content)
            
        return FileResponse(file_path)
    except Exception as e:
        print(f"Error fetching map {url}: {e}")
        return JSONResponse(status_code=500, content={"message": str(e)})






@app.get("/{path:path}")
async def serve_file(path: str):
    if path == "":
        path = "index.html"
    file_path = os.path.join("static", path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    else:
        return JSONResponse(status_code=404, content={"message": "File not found"})

if __name__ == "__main__":
    port = 8000 if len(sys.argv) < 2 else int(sys.argv[1])
    uvicorn.run(app, host="0.0.0.0", port=port)