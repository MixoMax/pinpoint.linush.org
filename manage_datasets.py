import requests # noqa
import json
import os
import time

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
API_ENDPOINT = "https://www.wikidata.org/w/api.php"
HEADERS = {
    "User-Agent": "PinpointGame/1.1 (https://github.com/pinpoint.linush.org; linush@example.com)"
}

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_header():
    clear_screen()
    print("==========================================")
    print("      Pinpoint Earth - Dataset Manager    ")
    print("      (With Search & Smart Filters)       ")
    print("==========================================")
    print()

def search_wikidata(query, type_filter='item'):
    """Searches Wikidata for an ID based on a string name."""
    try:
        params = {
            'action': 'wbsearchentities',
            'format': 'json',
            'language': 'en',
            'type': type_filter,
            'search': query,
            'limit': 5
        }
        r = requests.get(API_ENDPOINT, params=params, headers=HEADERS)
        data = r.json()
        return data.get('search', [])
    except Exception as e:
        print(f"Search failed: {e}")
        return []

def select_wikidata_item(prompt_text):
    """Loop until user finds and selects a valid Q-ID."""
    while True:
        user_input = input(f"{prompt_text} (or enter ID manually): ").strip()
        
        # If user knows the ID (e.g., Q183), let them use it
        if user_input.upper().startswith('Q') and user_input[1:].isdigit():
            return user_input.upper()
            
        print(f"Searching for '{user_input}'...")
        results = search_wikidata(user_input)
        
        if not results:
            print("No results found. Try again.")
            continue
            
        print("\nSelect an item:")
        for i, item in enumerate(results):
            desc = item.get('description', 'No description')
            print(f"{i+1}. {item['label']} ({item['id']}) - {desc}")
        print("0. Search again")
        
        try:
            choice = int(input("Choice: "))
            if choice == 0:
                continue
            if 1 <= choice <= len(results):
                selected = results[choice-1]
                print(f"Selected: {selected['label']} ({selected['id']})")
                return selected['id']
        except ValueError:
            pass
        print("Invalid selection.")

def fetch_sparql(query):
    try:
        print("Fetching data from Wikidata (this may take a moment)...")
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

def build_query(item_type_id, constraints, dataset_type, min_pop=0, exclude_dissolved=True, limit=100):
    sparql = "SELECT DISTINCT ?item ?itemLabel "
    
    if dataset_type == 'point':
        sparql += "?coord "
    elif dataset_type == 'polygon':
        sparql += "?geoShapeUrl "
        
    # We grab sitelinks to sort by popularity
    sparql += "?sitelinks "
        
    sparql += "WHERE {\n"
    
    # Main instance type
    sparql += f"  ?item wdt:P31/wdt:P279* wd:{item_type_id}.\n"
    
    # User Constraints
    for prop, val in constraints:
        is_negative = prop.startswith('-')
        if is_negative:
            prop = prop[1:]
            sparql += f"  FILTER NOT EXISTS {{ ?item wdt:{prop} wd:{val}. }}\n"
        else:
            sparql += f"  ?item wdt:{prop} wd:{val}.\n"
    
    # FILTER: Population
    if min_pop > 0:
        sparql += f"  ?item wdt:P1082 ?pop. FILTER(?pop >= {min_pop}).\n"

    # FILTER: Exclude Dissolved (Historical items)
    if exclude_dissolved:
        # P576 is "dissolved, abolished or demolished date"
        sparql += "  FILTER NOT EXISTS { ?item wdt:P576 ?end. }\n"
        
    # Type specific properties
    if dataset_type == 'point':
        sparql += "  ?item wdt:P625 ?coord.\n"
    elif dataset_type == 'polygon':
        sparql += "  ?item wdt:P3896 ?geoShapeUrl.\n"
        
    # Sitelinks for sorting relevance
    sparql += "  ?item wikibase:sitelinks ?sitelinks.\n"

    # Label service
    sparql += '  SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }\n'
    sparql += "}\n"
    
    # ORDER BY Relevance (Sitelinks)
    sparql += "ORDER BY DESC(?sitelinks)\n"
    sparql += f"LIMIT {limit}"
    
    return sparql

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

def main():
    print_header()
    
    # 1. Basic Info
    name = input("Enter dataset name (e.g., 'Big Cities in France'): ")
    description = input("Enter description: ")
    
    print("\nDataset Type:")
    print("1. Point (Locations like cities, buildings)")
    print("2. Polygon (Areas like countries, regions)")
    type_choice = input("Choice (1/2): ")
    dataset_type = 'point' if type_choice == '1' else 'polygon'
    
    # 2. Query Builder with Search
    print("\n--- Query Builder ---")
    print("Step 1: What type of item are we looking for?")
    # REPLACED manual input with search
    item_type_id = select_wikidata_item("Search for type (e.g. 'City', 'Castle')")
    
    constraints = []
    while True:
        print("\nStep 2: Add location constraints? (e.g., 'Located in Germany')")
        add = input("Add constraint? (y/n): ").lower()
        if add != 'y':
            break
            
        print("First, search for the Property (e.g. type 'Country' or 'Continent')")
        # Note: Searching for properties (P-ids) via API is tricky as they are mixed with items.
        # For simplicity, we ask for P-ID manually or assume standard P IDs, 
        # but here we'll stick to manual P-ID for precision, and Search for the Value.
        prop = input("Property ID (e.g., P17 for Country, P30 for Continent or -P17 for anything except for that contry): ").strip()
        if not (prop.startswith('P') or prop.startswith('-P')):
             print("Please enter a valid Property ID (starts with P or -P).")
             continue

        val = select_wikidata_item(f"Now search for the Value for {prop} (e.g. 'Germany', 'Asia')")
        constraints.append((prop, val))

    # 3. New Filters
    print("\n--- Filters ---")
    min_pop = 0
    pop_input = input("Minimum Population (Enter 0 to ignore): ").strip()
    if pop_input.isdigit():
        min_pop = int(pop_input)

    exclude_dissolved = True
    hist_input = input("Exclude dissolved/historical items (e.g. Roman Empire)? (y/n): ").lower()
    if hist_input == 'n':
        exclude_dissolved = False

    limit_input = input("How many items to fetch? (default 100): ").strip()
    limit = int(limit_input) if limit_input.isdigit() else 100

    # 4. Fetch and Preview
    query = build_query(item_type_id, constraints, dataset_type, min_pop, exclude_dissolved, limit)
    
    print("\nGenerated SPARQL Query:")
    # print(query) # Uncomment for debugging
    
    raw_data = fetch_sparql(query)
    if not raw_data:
        print("Failed to fetch data.")
        return
        
    parsed_data = parse_results(raw_data, dataset_type)
    
    print(f"\nFound {len(parsed_data)} items. Top 5 Most Relevant:")
    for i, item in enumerate(parsed_data[:5]):
        print(f"{i+1}. {item['label']} (ID: {item['id']})")
        
    if len(parsed_data) == 0:
        print("No results found. Try lowering population limit or removing constraints.")
        return

    confirm = input("\nSave this dataset? (y/n): ").lower()
    if confirm != 'y':
        print("Aborted.")
        return
        
    # 5. Configuration & Save (Unchanged)
    prompt_template = input("Enter prompt template (use {label} for name): ")
    if not prompt_template:
        prompt_template = "Where is {label}?"
    sub_prompt_template = input("Enter sub-prompt template (optional): ")
    
    filename = f"dataset_{int(time.time())}.json"
    filepath = os.path.join("static", filename)
    
    # Ensure static directory exists
    os.makedirs("static", exist_ok=True)
    
    with open(filepath, "w") as f:
        json.dump(parsed_data, f, indent=2)
        
    new_entry = {
        "id": f"custom_{int(time.time())}",
        "name": name,
        "description": description,
        "type": dataset_type,
        "filename": filename,
        "prompt_template": prompt_template,
        "sub_prompt_template": sub_prompt_template,
        "data_keys": { "label": "label" }
    }
    
    if dataset_type == 'point':
        new_entry['data_keys'].update({'lat': 'lat', 'lng': 'lng'})
    else:
        new_entry['data_keys']['geoShapeUrl'] = 'geoShapeUrl'
        
    try:
        with open("datasets.json", "r") as f:
            datasets = json.load(f)
    except FileNotFoundError:
        datasets = []
        
    datasets.append(new_entry)
    
    with open("datasets.json", "w") as f:
        json.dump(datasets, f, indent=2)
        
    print(f"\nSuccess! Saved to {filename}")

if __name__ == "__main__":
    main()