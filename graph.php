<?php
/*

The code is designed to render an interactive graph (network visualization) in a web browser. It takes graph data (nodes and edges) as well as additional information (such as “axial items,” dimensions, and aspects) from PHP variables and makes them available to the client-side JavaScript. Users can interact with the graph by clicking on nodes or edges, using zoom controls, and performing searches. The interactivity is enhanced by toggling color schemes (for clusters/dimensions or aspects) and filtering relevant details in an information grid.

*/

// Database connection
$host = "localhost";
$user = "debritto";
$pass = "240197";
$db   = "dgt7_factors";

$mysqli = new mysqli($host, $user, $pass, $db);
if ($mysqli->connect_error) {
    $error_message = "Database connection error: " . $mysqli->connect_error;
} else {
    // Node query
    $query_nodes = "SELECT f.`factor`, d.`dimension` AS `factor_dimension`, a.`aspect` AS `factor_aspect` FROM `factors` f LEFT JOIN `dimensions` d ON f.`factor_dimension_id` = d.`dimension_id` LEFT JOIN `aspects` a ON f.`factor_aspect_id` = a.`aspect_id`";
    $result_nodes = $mysqli->query($query_nodes);
    if (!$result_nodes) {
        $error_message = "Error on nodes data retrieve: " . $mysqli->error;
    } else {
        $nodes = [];
        $dimensions = [];
        $aspects = [];
        while ($row = $result_nodes->fetch_assoc()) {
            $factor = strtolower($row['factor']);
            $dimension = $row['factor_dimension'];
            $aspect = $row['factor_aspect'];
            $nodes[] = [
                "data" => [
                    "id" => $factor,
                    "label" => $factor,
                    "dimension" => $dimension,
                    "aspect" => $aspect
                ]
            ];
            if (!in_array($dimension, $dimensions)) $dimensions[] = $dimension;
            if (!in_array($aspect, $aspects)) $aspects[] = $aspect;
        }

        // Edge query
        $query_edges = "SELECT source_factor, target_factor, link_description, relation_type, link_order FROM matrix ORDER BY link_order";
        $result_edges = $mysqli->query($query_edges);
        if (!$result_edges) {
            $error_message = "Error on links data retrieve: " . $mysqli->error;
        } else {
            $edges = [];
            $edge_groups = [];
            while ($row = $result_edges->fetch_assoc()) {
                $source = strtolower(trim($row['source_factor']));
                $target = strtolower(trim($row['target_factor']));
                $link_desc = $row['link_description'];
                $relation = trim($row['relation_type']);
                $order = $row['link_order'];
                $edge_key = "$source|$target";
                if (!isset($edge_groups[$edge_key])) {
                    $edge_groups[$edge_key] = [
                        'source' => $source,
                        'target' => $target,
                        'relations' => [],
                        'descriptions' => [],
                        'order' => $order
                    ];
                }
                $edge_groups[$edge_key]['relations'][] = $relation;
                $edge_groups[$edge_key]['descriptions'][] = $link_desc;
            }
            foreach ($edge_groups as $key => $group) {
                $relation_types = array_unique($group['relations']);
                $edges[] = [
                    "data" => [
                        "source" => $group['source'],
                        "target" => $group['target'],
                        "relation_type" => (count($relation_types) > 1) ? "many" : $relation_types[0],
                        "all_relations" => $relation_types,
                        "link_description" => implode(", ", array_filter($group['descriptions'])),
                        "link_order" => $group['order']
                    ]
                ];
            }
        }

        // Axial items query
        $query_items = "SELECT bibliographic_ref, factor, axial_factor, citation, description 
                        FROM axial_items";
        $result_items = $mysqli->query($query_items);
        if (!$result_items) {
            $error_message = "Error on items' data retrieve: " . $mysqli->error;
        } else {
            $items = [];
            while ($row = $result_items->fetch_assoc()) {
                $items[] = [
                    'bibliographic_ref' => $row['bibliographic_ref'],
                    'factor' => strtolower($row['factor']),
                    'axial_factor' => strtolower($row['axial_factor']),
                    'citation' => $row['citation'],
                    'description' => $row['description']
                ];
            }
        }
    }
    $mysqli->close();
}

$graph_data = isset($error_message) ? [] : ["nodes" => $nodes, "edges" => $edges];
$error = isset($error_message) ? $error_message : null;
$axial_items = isset($items) ? $items : [];
?>

<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rede Interativa com Cytoscape.js</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.23.0/cytoscape.min.js"></script>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            height: 100vh;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        #cy {
            width: 100%;
            height: 70%;
            border: 1px solid #ccc;
            flex-grow: 1;
        }
        #info-container {
            height: 30%;
            min-height: 50px;
            max-height: 80%;
            background-color: #f8f9fa;
            border-top: 1px solid #dee2e6;
            display: flex;
            flex-direction: column;
            resize: vertical;
            overflow: hidden;
            position: relative;
        }
        #info-header {
            /* padding: 5px 10px; */
            background-color: #e9ecef;
            border-bottom: 1px solid #dee2e6;
            text-align: center;
            font-size: 14px;
        }
        #info-grid-container {
            flex-grow: 1;
            overflow-y: auto;
            padding: 1px;
        }
        #info-grid {
            width: 100%;
            font-size: 12px;
        }
        #info-grid th, #info-grid td {
            padding: 2px;
            vertical-align: top;
        }
        #zoom-controls {
            position: absolute;
            top: 10px;
            right: 10px;
            z-index: 1000;
        }
        #search-container {
            position: absolute;
            top: 10px;
            left: 10px;
            z-index: 1000;
            width: 250px;
        }
        #dimension-legend-container {
            position: absolute;
            top: 50px;
            left: 10px;
            z-index: 1000;
            width: 250px;
            background: rgba(255, 255, 255, 0.9);
            padding: 5px;
            border-radius: 5px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.2);
            font-size: 12px;
        }
        #aspect-legend-container {
            position: absolute;
            top: 50px;
            left: 10px;
            z-index: 1000;
            width: 250px;
            background: rgba(255, 255, 255, 0.9);
            padding: 5px;
            border-radius: 5px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.2);
            font-size: 12px;
            display: none;
        }
        #checkboxes-container {
            height: 4%;
            padding: 1px;
            display: flex;
            align-items: center;
            gap: 20px;
            background-color: #f8f9fa;
            border-bottom: 1px solid #dee2e6;
        }
        .legend-item {
            display: flex;
            align-items: center;
            margin-bottom: 3px;
        }
        .legend-color {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 5px;
        }
    </style>
</head>
<body>
    <div id="cy" aria-label="Graph visualization area"></div>
    
    <div id="checkboxes-container" class="form-check-container" role="group" aria-label="Graph controls">
        <div class="form-check">
            <input class="form-check-input" type="checkbox" id="activate-clusters" checked aria-label="Toggle dimension clusters">
            <label class="form-check-label" for="activate-clusters">Activate dimension clusters</label>
        </div>
        <div class="form-check">
            <input class="form-check-input" type="checkbox" id="activate-aspects" aria-label="Toggle aspects">
            <label class="form-check-label" for="activate-aspects">Activate aspects</label>
        </div>
        <div class="form-check">
            <input class="form-check-input" type="checkbox" id="isolate-mode" aria-label="Toggle isolate mode">
            <label class="form-check-label" for="isolate-mode">Isolate Mode</label>
        </div>
    </div>
    
    <div id="info-container" role="region" aria-label="Detailed information grid">
        <div id="info-header">
            <span>Click on a factor or link to see the details</span>
        </div>
        <div id="info-grid-container">
            <table id="info-grid" class="table table-striped table-bordered">
                <thead>
                    <tr>
                        <th>Source</th>
                        <th>Factor</th>
                        <th>Topic</th>
                        <th>Citation</th>
                        <th>Description</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </div>
    
    <div id="zoom-controls" class="btn-group-vertical" role="group" aria-label="Zoom controls">
        <button id="zoom-in" class="btn btn-primary" aria-label="Zoom in">+</button>
        <button id="zoom-out" class="btn btn-primary" aria-label="Zoom out">-</button>
        <button id="reset-zoom" class="btn btn-secondary" aria-label="Reset zoom and isolate mode">Reset</button>
    </div>
    
    <div id="search-container">
        <input type="text" id="search-input" class="form-control" placeholder="Search factor..." aria-label="Search nodes">
    </div>
    
    <div id="dimension-legend-container" aria-label="Dimension legend"></div>
    <div id="aspect-legend-container" aria-label="Aspect legend"></div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz" crossorigin="anonymous"></script>
    <script>
        var graphData = <?php echo json_encode($graph_data); ?>;
        var errorMessage = <?php echo json_encode($error); ?>;
        var axialItems = <?php echo json_encode($axial_items); ?>;

        if (errorMessage) {
            document.getElementById('info-header').innerText = errorMessage;
            document.getElementById('info-header').style.backgroundColor = '#f8d7da';
            console.error(errorMessage);
        } else {

                var dimensionColors = {
                    <?php
                    $dimensionVivid = ['#16ad85', '#d5006d', '#3f51b5', '#4caf50', '#2980b9', '#2c3e50', '#bb4513', '#d2691e', "#581845", "#ff5733", "#daf7a6", "#ffc300", "#bae1ff", "#d4ac0d", "#ffea00", "#ff5e3a" ];
                    $i = 0;
                    foreach ($dimensions as $dim) {
                        echo "'$dim': '{$dimensionVivid[$i % count($dimensionVivid)]}',";
                        $i++;
                    }
                    ?>
                };

            var aspectColors = {
                <?php 
                $aspectVivid = ['#16ad85', '#d5006d', '#3f51b5', '#4caf50', '#2980b9', '#2c3e50', '#bb4513', '#d2691e', "#581845", "#ff5733", "#daf7a6", "#ffc300", "#bae1ff", "#d4ac0d", "#ffea00", "#ff5e3a" ];
                $i = 0;
                foreach ($aspects as $asp) {
                    echo "'$asp': '{$aspectVivid[$i % count($aspectVivid)]}',";
                    $i++;                 
                }
                ?>
            };

            var dimensionLegend = document.getElementById('dimension-legend-container');
            dimensionLegend.innerHTML = '<h6>Dimensions</h6>';
            Object.keys(dimensionColors).sort().forEach(dim => {
                var item = document.createElement('div');
                item.className = 'legend-item';
                item.innerHTML = `
                    <div class="legend-color" style="background-color: ${dimensionColors[dim]}"></div>
                    <span>${dim}</span>
                `;
                dimensionLegend.appendChild(item);
            });

            var aspectLegend = document.getElementById('aspect-legend-container');
            aspectLegend.innerHTML = '<h6>Aspects</h6>';
            Object.keys(aspectColors).sort().forEach(asp => {
                var item = document.createElement('div');
                item.className = 'legend-item';
                item.innerHTML = `
                    <div class="legend-color" style="background-color: ${aspectColors[asp]}"></div>
                    <span>${asp}</span>
                `;
                aspectLegend.appendChild(item);
            });

            var cy = cytoscape({
                container: document.getElementById('cy'),
                elements: [...graphData.nodes, ...graphData.edges],
                style: [
                    {
                        selector: 'node',
                        style: {
                            'background-color': '#f5f5dc',
                            'label': 'data(label)',
                            'color': '#fff',
                            'text-valign': 'center',
                            'text-halign': 'center',
                            'font-size': '9px',
                            'width': function(node) { return 30 + (node.degree() * 1.5); }, // Tamanho dos nodes
                            'height': function(node) { return 30 + (node.degree() * 1.5); },
                            'text-wrap': 'wrap',
                            'text-max-width': '45px',
                            'border-width': '6px',
                            'border-color': function(node) {
                                var dim = node.data('dimension');
                                var asp = node.data('aspect');
                                var degree = node.degree();

                                if (document.getElementById('activate-clusters').checked) {
                                    return dimensionColors[dim];
                                } else if (document.getElementById('activate-aspects').checked) {
                                    return aspectColors[asp];
                                } else {
                                    return '#f5f5dc';
                                }
                            },
                            'text-background-color': '#808080',
                            'text-background-opacity': 0.7,
                            'text-background-padding': '2px',
                            'text-background-shape': 'roundrectangle'
                        }
                    },
                    {
                        selector: 'edge',
                        style: {
                            'width': 2,
                            'line-color': '#666',
                            'curve-style': 'bezier',
                            'label': 'data(relation_type)',
                            'font-size': '8px',
                            'text-rotation': 'autorotate',
                            'text-margin-y': -15,
                            'text-margin-x': 0,
                            'color': '#333',
                            'text-background-color': '#fff',
                            'text-background-opacity': 0.7,
                            'text-background-padding': '2px',
                            'target-arrow-shape': 'triangle',
                            'target-arrow-color': '#666',
                            'arrow-scale': 1.5
                        }
                    },
                    {
                        selector: 'edge[relation_type = "many"]',
                        style: { 'width': 4 }
                    },
                    {
                        selector: 'edge[relation_type = "causes"]',
                        style: { 'color': '#FF0000' }
                    },
                    {
                        selector: '.hidden',
                        style: { 'display': 'none' }
                    }
                ],
                layout: {
                    name: 'cose',
                    animate: false, // Disable animation for large graphs
                    fit: true,
                    idealEdgeLength: 250, // Aumenta o comprimento das arestas para maior separação
                    nodeRepulsion: 12000000, // Aumenta a repulsão entre nós
                    padding: 20, // Mais espaço nas bordas do canvas
                    gravity: 0.005, // Reduz a força que puxa para o centro
                    nodeDimensionsIncludeLabels: true, // Considera rótulos para evitar sobreposição
                    randomize: true, // Inicia com posições mais espalhadas
                    numIter: 250, // Reduce iterations for faster convergence
                    refresh: 1 // Lower refresh rate for performance
                },
                userPanningEnabled: true,
                boxSelectionEnabled: false
            });

            var currentIsolatedNode = null;

            function resetColors() {
                cy.nodes().style({
                    'background-color': '#f5f5dc',
                    'border-color': function(node) {
                        var dim = node.data('dimension');
                        var asp = node.data('aspect');
                        var degree = node.degree();
                        if (document.getElementById('activate-clusters').checked) {
                            return dimensionColors[dim];
                        } else if (document.getElementById('activate-aspects').checked) {
                            return aspectColors[asp];
                        } else {
                            return '#f5f5dc';
                        }
                    }
                });
                cy.edges().style({
                    'line-color': '#666',
                    'target-arrow-color': '#666',
                    'color': '#333'
                });
                cy.edges('[relation_type = "causes"]').style('color', '#FF0000');
            }

            function showAllElements() {
                cy.elements().removeClass('hidden');
                cy.elements().style({'opacity': 1});
                currentIsolatedNode = null;
            }

            function hideUnrelatedElements(selectedNode) {
                cy.elements().addClass('hidden');
                cy.elements().style({'opacity': 0.2});
                selectedNode.removeClass('hidden');
                selectedNode.style({'opacity': 1});
                var connectedEdges = selectedNode.connectedEdges();
                connectedEdges.removeClass('hidden');
                connectedEdges.style({'opacity': 1});
                var connectedNodes = connectedEdges.connectedNodes();
                connectedNodes.removeClass('hidden');
                connectedNodes.style({'opacity': 1});
                currentIsolatedNode = selectedNode;

                var layout = cy.layout({
                    name: 'cose',
                    animate: false,
                    fit: true,
                    idealEdgeLength: 250,
                    nodeRepulsion: 12000000,
                    padding: 20,
                    gravity: 0.005,
                    numIter: 250, // Reduce iterations for faster convergence
                    refresh: 1 // Lower refresh rate for performance
                });
                layout.run();
            }

            function updateGrid(rows) {
                var tbody = document.getElementById('info-grid').getElementsByTagName('tbody')[0];
                tbody.innerHTML = '';
                if (rows.length === 0) {
                    var row = tbody.insertRow();
                    var cell = row.insertCell();
                    cell.colSpan = 5;
                    cell.textContent = 'No items found.';
                    cell.style.textAlign = 'center';
                } else {
                    rows.forEach(item => {
                        var row = tbody.insertRow();
                        row.insertCell().textContent = item.bibliographic_ref || '';
                        row.insertCell().textContent = item.factor || '';
                        row.insertCell().textContent = item.axial_factor || '';
                        row.insertCell().textContent = item.citation || '';
                        row.insertCell().textContent = item.description || '';
                    });
                }
            }

            cy.on('tap', 'edge', function(evt) {
                evt.preventDefault();
                evt.stopPropagation();
                var edge = evt.target;
                edge.ungrabify();
                var isolateMode = document.getElementById('isolate-mode').checked;

                resetColors();
                edge.style({
                    'line-color': '#FF4136',
                    'target-arrow-color': '#FF4136'
                });
                var sourceNode = edge.source();
                var targetNode = edge.target();
                sourceNode.style('background-color', '#FF851B');
                targetNode.style('background-color', '#FF851B');

                var source = edge.data('source');
                var target = edge.data('target');
                var relation = edge.data('relation_type') || 'Without relation';
                var desc = edge.data('link_description') ? ` (${edge.data('link_description')})` : '';
                var headerText = relation === 'many' ?
                    `Link: ${source} → ${target}${desc} (Relations: ${edge.data('all_relations').join(', ')})` :
                    `Link: ${source} → ${relation}  → ${target}${desc}`;
                document.getElementById('info-header').textContent = headerText;

                if (isolateMode && currentIsolatedNode) {
                    hideUnrelatedElements(currentIsolatedNode);
                } else {
                    showAllElements();
                }

                var filteredItems = axialItems.filter(item =>
                    item.factor === source || item.factor === target
                );
                updateGrid(filteredItems);
            });

            cy.on('tap', 'node', function(evt) {
                evt.preventDefault();
                evt.stopPropagation();
                var node = evt.target;
                var isolateMode = document.getElementById('isolate-mode').checked;

                resetColors();
                node.style('background-color', '#FF4136');
                var connectedNodes = node.connectedEdges().connectedNodes();
                connectedNodes.style('background-color', '#FF851B');

                document.getElementById('info-header').textContent = `Factor: ${node.data('label')} (Degree: ${node.degree()})`;

                if (isolateMode) {
                    hideUnrelatedElements(node);
                } else {
                    showAllElements();
                }

                var filteredItems = axialItems.filter(item => item.factor === node.data('label'));
                updateGrid(filteredItems);
            });

            cy.on('grab', 'edge', function(evt) {
                evt.target.ungrabify();
            });

            cy.on('tapend', 'edge', function(evt) {
                evt.target.grabify();
            });

            document.getElementById('zoom-in').addEventListener('click', function() {
                cy.zoom(cy.zoom() * 1.1);
                cy.center();
            });

            document.getElementById('zoom-out').addEventListener('click', function() {
                cy.zoom(cy.zoom() * 0.9);
                cy.center();
            });

            document.getElementById('reset-zoom').addEventListener('click', function() {
                resetColors();
                showAllElements();
                document.getElementById('isolate-mode').checked = false;
                cy.fit();
                document.getElementById('info-header').textContent = 'Click on a factor or link to see the details.';
                updateGrid([]);
            });

            document.getElementById('search-input').addEventListener('input', function(e) {
                var searchValue = e.target.value.toLowerCase().trim();
                var isolateMode = document.getElementById('isolate-mode').checked;

                resetColors();
                if (isolateMode && currentIsolatedNode) {
                    var foundNode = currentIsolatedNode.connectedEdges().connectedNodes().filter(function(node) {
                        return node.data('label') === searchValue;
                    })[0] || (currentIsolatedNode.data('label') === searchValue ? currentIsolatedNode : null);

                    if (foundNode) {
                        foundNode.style('background-color', '#FF4136');
                        var connectedNodes = foundNode.connectedEdges().connectedNodes();
                        connectedNodes.style('background-color', '#FF851B');
                        document.getElementById('info-header').textContent = `Factor found (isolated): ${foundNode.data('label')} (Degree: ${foundNode.degree()})`;
                        cy.center(foundNode);
                        var filteredItems = axialItems.filter(item => item.factor === foundNode.data('label'));
                        updateGrid(filteredItems);
                    } else {
                        document.getElementById('info-header').textContent = `Factor "${searchValue}" not found in isolated mode.`;
                        updateGrid([]);
                    }
                } else {
                    showAllElements();
                    if (searchValue) {
                        var foundNode = cy.nodes().filter(function(node) {
                            return node.data('label') === searchValue;
                        })[0];

                        if (foundNode) {
                            foundNode.style('background-color', '#FF4136');
                            var connectedNodes = foundNode.connectedEdges().connectedNodes();
                            connectedNodes.style('background-color', '#FF851B');
                            document.getElementById('info-header').textContent = `Factor found: ${foundNode.data('label')} (Degree: ${foundNode.degree()})`;
                            cy.center(foundNode);
                            var filteredItems = axialItems.filter(item => item.factor === foundNode.data('label'));
                            updateGrid(filteredItems);
                        } else {
                            document.getElementById('info-header').textContent = `Factor "${searchValue}" not found.`;
                            updateGrid([]);
                        }
                    } else {
                        document.getElementById('info-header').textContent = 'Click on a factor or link to see the details.';
                        updateGrid([]);
                    }
                }
            });

            var clusterCheckbox = document.getElementById('activate-clusters');
            var aspectCheckbox = document.getElementById('activate-aspects');

            dimensionLegend.style.display = 'block';
            aspectLegend.style.display = 'none';

            clusterCheckbox.addEventListener('change', function(e) {
                if (e.target.checked) {
                    aspectCheckbox.checked = false;
                    resetColors();
                    if (currentIsolatedNode) hideUnrelatedElements(currentIsolatedNode);
                    dimensionLegend.style.display = 'block';
                    aspectLegend.style.display = 'none';
                } else {
                    resetColors();
                    if (currentIsolatedNode) hideUnrelatedElements(currentIsolatedNode);
                    dimensionLegend.style.display = 'none';
                    if (aspectCheckbox.checked) {
                        aspectLegend.style.display = 'block';
                    }
                }
            });

            aspectCheckbox.addEventListener('change', function(e) {
                if (e.target.checked) {
                    clusterCheckbox.checked = false;
                    resetColors();
                    if (currentIsolatedNode) hideUnrelatedElements(currentIsolatedNode);
                    aspectLegend.style.display = 'block';
                    dimensionLegend.style.display = 'none';
                } else {
                    resetColors();
                    if (currentIsolatedNode) hideUnrelatedElements(currentIsolatedNode);
                    aspectLegend.style.display = 'none';
                    if (clusterCheckbox.checked) {
                        dimensionLegend.style.display = 'block';
                    }
                }
            });
        }
    </script>
</body>
</html>