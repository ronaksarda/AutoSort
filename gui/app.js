document.addEventListener('DOMContentLoaded', () => {
    // Buttons
    const btnDryRun = document.getElementById('btn-dryrun');
    const btnRun = document.getElementById('btn-run');
    const btnBrowseSource = document.getElementById('btn-browse-source');
    const btnBrowseOut = document.getElementById('btn-browse-out');
    
    // Inputs
    const sourceInput = document.getElementById('source-input');
    const outInput = document.getElementById('out-input');
    const modeToggle = document.getElementById('mode-toggle');
    const dedupToggle = document.getElementById('dedup-toggle');
    
    // Stats & Panels
    const miniStats = document.getElementById('mini-stats');
    const statFiles = document.getElementById('stat-files');
    const statTime = document.getElementById('stat-time');
    const statRate = document.getElementById('stat-rate');
    
    const welcomeView = document.getElementById('welcome-view');
    const resultsPanel = document.getElementById('results-panel');
    const consolePanel = document.getElementById('console-panel');
    const logOutput = document.getElementById('log-output');

    // Category Card Counts
    const countImages = document.getElementById('count-images');
    const countDocs = document.getElementById('count-docs');
    const countAudio = document.getElementById('count-audio');
    const countArchives = document.getElementById('count-archives');
    const countCode = document.getElementById('count-code');
    const countOthers = document.getElementById('count-others');

    function setNumber(element, value, isFloat = false) {
        if (!element) return;
        if (isFloat) {
            element.textContent = value.toFixed(2) + 's';
        } else {
            element.textContent = value.toLocaleString();
        }
    }

    async function executeSort(isDryRun) {
        const source = sourceInput.value.trim();
        const out = outInput.value.trim();
        
        if (!source) {
            alert("Please select or enter a Source Folder.");
            return;
        }

        if (!isDryRun && !out) {
            alert("Please select or enter a Destination Folder for sorting.");
            return;
        }

        // Show results & console panels
        if (welcomeView) welcomeView.style.display = 'none';
        if (resultsPanel) resultsPanel.style.display = 'block';
        if (consolePanel) consolePanel.style.display = 'block';
        
        if (logOutput) logOutput.textContent = 'Initializing file scanner and hashing engine...\n';
        
        // Disable controls and update button states
        btnDryRun.disabled = true;
        btnRun.disabled = true;
        const originalDryText = btnDryRun.textContent;
        const originalRunText = btnRun.textContent;
        if (isDryRun) {
            btnDryRun.textContent = 'Analyzing...';
        } else {
            btnRun.textContent = 'Organizing...';
        }

        const payload = {
            source: source,
            out: out,
            is_dry_run: isDryRun,
            copy_mode: modeToggle.checked,
            dedup: dedupToggle.checked
        };

        try {
            const response = await fetch('/api/sort', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const result = await response.json();
            
            if (response.ok) {
                // Show statistics panel
                if (miniStats) miniStats.style.display = 'flex';
                
                // Set stats values
                setNumber(statFiles, result.data.total_files);
                setNumber(statTime, result.data.elapsed_sec, true);
                setNumber(statRate, Math.round(result.data.throughput));

                // Process and distribute categories counts
                const catData = result.data.by_category || {};
                const imagesCount = catData["Images"] || 0;
                const docsCount = (catData["Documents"] || 0) + (catData["Data"] || 0);
                const audioCount = (catData["Audio"] || 0) + (catData["Videos"] || 0);
                const archivesCount = catData["Archives"] || 0;
                const codeCount = catData["Code"] || 0;
                
                // Collect remaining other categories
                let othersCount = 0;
                const knownKeys = ["Images", "Documents", "Data", "Audio", "Videos", "Archives", "Code"];
                for (const key in catData) {
                    if (!knownKeys.includes(key)) {
                        othersCount += catData[key];
                    }
                }

                // Show category numbers
                setNumber(countImages, imagesCount);
                setNumber(countDocs, docsCount);
                setNumber(countAudio, audioCount);
                setNumber(countArchives, archivesCount);
                setNumber(countCode, codeCount);
                setNumber(countOthers, othersCount);

                // Show formatted logs
                if (logOutput) logOutput.textContent = result.raw_log;
            } else {
                if (logOutput) logOutput.textContent = `[ERROR] ${result.error || 'Unknown server error.'}`;
            }
        } catch (error) {
            if (logOutput) logOutput.textContent = `[CONNECTION ERROR] Failed to connect to local server: ${error.message}`;
        } finally {
            btnDryRun.disabled = false;
            btnRun.disabled = false;
            btnDryRun.textContent = originalDryText;
            btnRun.textContent = originalRunText;
            
            // Scroll to results
            if (resultsPanel) {
                resultsPanel.scrollIntoView({ behavior: 'smooth' });
            }
        }
    }

    async function browseFolder(inputElement) {
        try {
            const response = await fetch('/api/browse');
            const result = await response.json();
            if (response.ok && result.path) {
                inputElement.value = result.path;
            }
        } catch (error) {
            console.error("Failed to browse folder", error);
        }
    }

    btnBrowseSource.addEventListener('click', () => browseFolder(sourceInput));
    btnBrowseOut.addEventListener('click', () => browseFolder(outInput));

    btnDryRun.addEventListener('click', () => executeSort(true));
    btnRun.addEventListener('click', () => executeSort(false));
});
