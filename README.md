# 🧭 Campus TCE — Smart Campus Navigation

A smart campus navigation web application designed to help students, faculty, and visitors navigate the campus of Thiagarajar College of Engineering (TCE), Madurai.

The project uses interactive maps, geographic data, route calculation, and landmark-based directions to help users find buildings, departments, and important campus locations.

## ✨ Features

### 🗺️ Interactive Campus Map
- Displays campus pathways and geographic features.
- Uses OpenStreetMap (OSM) and KML data.
- Provides an interactive map for exploring campus locations.

### 📍 Location Selection
- Select a starting point and destination.
- Find routes between mapped campus locations.
- Use recognizable campus landmarks for easier navigation.

### 🧭 Route Calculation
- Calculates walking routes using the campus pathway network.
- Uses Dijkstra's algorithm for shortest-path calculation.
- Provides alternative routes when available.
- Displays route distances and navigation information.

### 🚶 Landmark-Based Directions
- Generates step-by-step walking directions.
- Provides directional guidance along the selected route.
- Uses mapped landmarks to make navigation easier to understand.

### 📡 Current Location
- Supports browser-based geolocation where implemented.
- Allows users to identify their current position.
- Helps users navigate from their location to a selected destination.

### 🎨 Interactive User Interface
- Displays maps using Leaflet.
- Provides route visualization and location markers.
- Presents navigation information through a web interface.

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Backend logic and route calculation |
| Flask | Web application framework |
| HTML | Webpage structure |
| CSS | Interface styling |
| JavaScript | Frontend interactions |
| Leaflet.js | Interactive map visualization |
| OpenStreetMap | Geographic map data |
| OSM XML | Campus pathway data |
| KML | Geographic information and landmarks |
| Dijkstra's Algorithm | Shortest-path calculation |
| Git & GitHub | Version control and source code hosting |

## ⚙️ How It Works

1. **Load Campus Data:** Reads geographic information from the OSM and KML files.
2. **Display the Map:** Renders the campus map through Leaflet.
3. **Select Locations:** Allows users to choose a starting point and destination.
4. **Calculate Routes:** Processes the campus pathway network to find suitable routes.
5. **Generate Directions:** Provides route distances and walking instructions.
6. **Navigate the Campus:** Displays the selected route and relevant navigation information.

## 📂 Project Structure

```text
campus_tce/
├── app.py
├── map.osm
├── map.kml
├── requirements.txt
├── templates/
│   └── index.html
└── README.md
```

- `app.py` — Flask backend and route calculation logic.
- `map.osm` — OpenStreetMap data for campus pathways and geographic features.
- `map.kml` — Additional geographic data and campus landmarks.
- `requirements.txt` — Python dependencies.
- `templates/index.html` — Frontend interface and interactive map.
- `README.md` — Project documentation.

## 🚀 Getting Started

### Prerequisites

- Python 3
- Git
- A modern web browser

### 1. Clone the Repository

```bash
git clone https://github.com/nerdprog/campus_tce.git
cd campus_tce
```

### 2. Create a Virtual Environment

```bash
python -m venv venv
```

**Windows:**

```powershell
.\venv\Scripts\Activate.ps1
```

**Linux / macOS:**

```bash
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Application

```bash
python app.py
```

Open the application in your browser:

[http://127.0.0.1:5000](http://127.0.0.1:5000)

## 🔮 Future Improvements

- Voice-guided navigation.
- Automatic rerouting when users deviate from their route.
- Estimated walking time and arrival time.
- Improved campus location search.
- Accessibility-friendly walking routes.
- Enhanced mobile interface.
- Offline campus map support.
- Expanded campus landmarks and pathway coverage.

## 🎯 Project Goal

The goal of Campus TCE is to simplify campus navigation by combining interactive maps, geographic data, and route-planning algorithms in one user-friendly web application.

## 👨‍💻 Author

**GitHub:** [@nerdprog](https://github.com/nerdprog)

## 🔗 Repository

[View Campus TCE on GitHub](https://github.com/nerdprog/campus_tce)

---

*Making campus navigation easier, one landmark at a time.*
