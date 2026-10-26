'use strict';

document.addEventListener('DOMContentLoaded', () => {
  const state = { start: 'start', playing: 'playing', paused: 'paused', over: 'over' };
  const screens = {
    start: document.getElementById('start-screen'),
    playing: document.getElementById('game-screen'),
    over: document.getElementById('game-over'),
  };
  const form = document.getElementById('start-form');
  const nameInput = document.getElementById('player-name');
  const nameError = document.getElementById('name-error');
  const playButton = document.getElementById('play-button');
  const canvas = document.getElementById('game-canvas');
  const context = canvas?.getContext('2d');
  const scoreElement = document.getElementById('score');
  const finalScoreElement = document.getElementById('final-score');
  const highScoreElement = document.getElementById('high-score');
  const driverLabel = document.getElementById('driver-label');
  const pauseButton = document.getElementById('pause-button');
  const pauseOverlay = document.getElementById('pause-overlay');
  const resumeButton = document.getElementById('resume-button');
  const restartButton = document.getElementById('restart-button');
  const backButton = document.getElementById('back-button');
  const soundToggle = document.getElementById('sound-toggle');
  const motionToggle = document.getElementById('motion-toggle');
  const storageKey = 'neon-drift-best-score';
  const laneCount = 3;

  if (!canvas || !context || !form || !nameInput || !nameError || !playButton) return;

  let currentState = state.start;
  let animationFrame = 0;
  let lastFrameTime = 0;
  let score = 0;
  let bestScore = readBestScore();
  let speed = 230;
  let spawnTimer = 0;
  let speedTimer = 0;
  let roadOffset = 0;
  let driverName = 'Driver';
  let reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let soundEnabled = false;
  let audioContext = null;
  let width = 0;
  let height = 0;
  let pixelRatio = 1;
  let car = null;
  let obstacles = [];
  let touchStartX = null;

  function readBestScore() {
    try {
      return Number(localStorage.getItem(storageKey)) || 0;
    } catch {
      return 0;
    }
  }

  function saveBestScore(value) {
    try {
      localStorage.setItem(storageKey, String(value));
    } catch {
      // The current run still works when browser storage is disabled.
    }
  }

  function showScreen(name) {
    Object.entries(screens).forEach(([key, screen]) => {
      const isVisible = key === name;
      screen.classList.toggle('active', isVisible);
      screen.setAttribute('aria-hidden', String(!isVisible));
    });
  }

  function laneWidth() {
    return width / laneCount;
  }

  function resizeCanvas() {
    const bounds = canvas.getBoundingClientRect();
    if (!bounds.width) return;

    width = bounds.width;
    height = bounds.height;
    pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);

    if (car) positionCar();
    obstacles.forEach((obstacle) => {
      obstacle.width = laneWidth() * 0.48;
      obstacle.height = obstacle.width * 1.42;
      obstacle.x = laneX(obstacle.lane, obstacle.width);
    });
  }

  function laneX(lane, objectWidth) {
    return lane * laneWidth() + (laneWidth() - objectWidth) / 2;
  }

  function positionCar() {
    car.width = laneWidth() * 0.48;
    car.height = car.width * 1.48;
    car.x = laneX(car.lane, car.width);
    car.y = height - car.height - 28;
  }

  function startRun() {
    driverName = nameInput.value.trim().slice(0, 12) || 'Driver';
    nameInput.value = driverName;
    nameError.textContent = '';
    if (driverLabel) driverLabel.textContent = driverName.toUpperCase();
    score = 0;
    speed = 230;
    spawnTimer = 700;
    speedTimer = 0;
    roadOffset = 0;
    obstacles = [];
    car = { lane: 1, x: 0, y: 0, width: 0, height: 0 };
    currentState = state.playing;
    showScreen('playing');
    if (pauseOverlay) pauseOverlay.setAttribute('aria-hidden', 'true');
    resizeCanvas();
    updateScore();
    lastFrameTime = 0;
    playTone(520, 0.08);
    animationFrame = requestAnimationFrame(gameLoop);
  }

  function updateScore() {
    if (scoreElement) scoreElement.textContent = String(Math.floor(score));
  }

  function roundRect(x, y, rectWidth, rectHeight, radius, color) {
    const r = Math.min(radius, rectWidth / 2, rectHeight / 2);
    context.fillStyle = color;
    context.beginPath();
    context.moveTo(x + r, y);
    context.arcTo(x + rectWidth, y, x + rectWidth, y + rectHeight, r);
    context.arcTo(x + rectWidth, y + rectHeight, x, y + rectHeight, r);
    context.arcTo(x, y + rectHeight, x, y, r);
    context.arcTo(x, y, x + rectWidth, y, r);
    context.closePath();
    context.fill();
  }

  function drawRoad() {
    context.fillStyle = '#101723';
    context.fillRect(0, 0, width, height);

    const shoulder = Math.max(7, width * 0.035);
    context.fillStyle = '#202334';
    context.fillRect(0, 0, shoulder, height);
    context.fillRect(width - shoulder, 0, shoulder, height);

    context.strokeStyle = 'rgba(54, 245, 240, .18)';
    context.lineWidth = 1;
    for (let lane = 1; lane < laneCount; lane += 1) {
      const x = laneWidth() * lane;
      context.beginPath();
      context.setLineDash(reducedMotion ? [] : [20, 18]);
      context.lineDashOffset = -roadOffset;
      context.moveTo(x, 0);
      context.lineTo(x, height);
      context.stroke();
    }
    context.setLineDash([]);

    const glow = context.createLinearGradient(0, 0, width, 0);
    glow.addColorStop(0, 'rgba(255, 67, 182, .12)');
    glow.addColorStop(0.5, 'rgba(54, 245, 240, .025)');
    glow.addColorStop(1, 'rgba(255, 67, 182, .12)');
    context.fillStyle = glow;
    context.fillRect(0, 0, width, height);
  }

  function drawCar() {
    if (!car) return;
    context.save();
    context.shadowColor = '#36f5f0';
    context.shadowBlur = 16;
    roundRect(car.x, car.y, car.width, car.height, car.width * 0.24, '#36f5f0');
    context.shadowBlur = 0;
    roundRect(car.x + car.width * 0.17, car.y + car.height * 0.18, car.width * 0.66, car.height * 0.34, 8, '#15333c');
    roundRect(car.x + car.width * 0.22, car.y + car.height * 0.68, car.width * 0.56, car.height * 0.13, 5, '#b1fffb');
    context.fillStyle = '#ff43b6';
    context.fillRect(car.x - 3, car.y + car.height * 0.18, 4, car.height * 0.22);
    context.fillRect(car.x + car.width - 1, car.y + car.height * 0.18, 4, car.height * 0.22);
    context.restore();
  }

  function drawObstacles() {
    obstacles.forEach((obstacle) => {
      context.save();
      context.shadowColor = '#ff43b6';
      context.shadowBlur = 12;
      roundRect(obstacle.x, obstacle.y, obstacle.width, obstacle.height, obstacle.width * 0.2, '#ff43b6');
      context.shadowBlur = 0;
      roundRect(obstacle.x + obstacle.width * 0.15, obstacle.y + obstacle.height * 0.16, obstacle.width * 0.7, obstacle.height * 0.23, 5, '#542344');
      context.fillStyle = '#ffe66d';
      context.fillRect(obstacle.x + obstacle.width * 0.1, obstacle.y + obstacle.height * 0.84, obstacle.width * 0.24, 4);
      context.fillRect(obstacle.x + obstacle.width * 0.66, obstacle.y + obstacle.height * 0.84, obstacle.width * 0.24, 4);
      context.restore();
    });
  }

  function moveCar(direction) {
    if (currentState !== state.playing || !car) return;
    car.lane = Math.max(0, Math.min(laneCount - 1, car.lane + direction));
    positionCar();
    playTone(direction < 0 ? 360 : 430, 0.035);
  }

  function overlaps(a, b) {
    const insetX = a.width * 0.2;
    const insetY = a.height * 0.12;
    const otherInsetX = b.width * 0.14;
    return a.x + insetX < b.x + b.width - otherInsetX
      && a.x + a.width - insetX > b.x + otherInsetX
      && a.y + insetY < b.y + b.height
      && a.y + a.height - insetY > b.y;
  }

  function endRun() {
    if (currentState !== state.playing) return;
    currentState = state.over;
    cancelAnimationFrame(animationFrame);
    const finishedScore = Math.floor(score);
    const isNewBest = finishedScore > bestScore;
    bestScore = Math.max(bestScore, finishedScore);
    saveBestScore(bestScore);
    if (finalScoreElement) finalScoreElement.textContent = `${driverName}, you drove ${finishedScore} m.`;
    if (highScoreElement) highScoreElement.textContent = isNewBest ? `New personal best: ${bestScore} m` : `Personal best: ${bestScore} m`;
    showScreen('over');
    restartButton?.focus();
    playTone(180, 0.22);
  }

  function gameLoop(timestamp) {
    if (currentState !== state.playing) return;
    if (!width || !height) resizeCanvas();

    const delta = lastFrameTime ? Math.min((timestamp - lastFrameTime) / 1000, 0.05) : 0;
    lastFrameTime = timestamp;
    if (!reducedMotion) roadOffset = (roadOffset + speed * delta) % 38;
    score += delta * 10;
    spawnTimer -= delta * 1000;
    speedTimer += delta * 1000;

    if (spawnTimer <= 0) {
      const lane = Math.floor(Math.random() * laneCount);
      const obstacleWidth = laneWidth() * 0.48;
      const obstacleHeight = obstacleWidth * 1.42;
      const laneIsClear = obstacles.every((obstacle) => obstacle.lane !== lane || obstacle.y > obstacleHeight * 1.5);
      if (laneIsClear) {
        obstacles.push({
          lane,
          x: laneX(lane, obstacleWidth),
          y: -obstacleHeight,
          width: obstacleWidth,
          height: obstacleHeight,
        });
      }
      spawnTimer = 780 + Math.random() * 680;
    }

    obstacles.forEach((obstacle) => { obstacle.y += speed * delta; });
    obstacles = obstacles.filter((obstacle) => obstacle.y < height + obstacle.height);
    if (speedTimer > 12000) {
      speed = Math.min(speed + 18, 420);
      speedTimer = 0;
    }

    drawRoad();
    drawObstacles();
    drawCar();
    updateScore();

    if (obstacles.some((obstacle) => overlaps(car, obstacle))) {
      endRun();
      return;
    }
    animationFrame = requestAnimationFrame(gameLoop);
  }

  function pauseGame() {
    if (currentState !== state.playing) return;
    currentState = state.paused;
    cancelAnimationFrame(animationFrame);
    pauseOverlay?.setAttribute('aria-hidden', 'false');
    resumeButton?.focus();
  }

  function resumeGame() {
    if (currentState !== state.paused) return;
    currentState = state.playing;
    pauseOverlay?.setAttribute('aria-hidden', 'true');
    lastFrameTime = 0;
    animationFrame = requestAnimationFrame(gameLoop);
  }

  function playTone(frequency, duration) {
    if (!soundEnabled) return;
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return;
      audioContext ||= new AudioContextClass();
      if (audioContext.state === 'suspended') audioContext.resume();
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = 'sine';
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.08, audioContext.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + duration);
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      oscillator.start();
      oscillator.stop(audioContext.currentTime + duration);
    } catch {
      // Gameplay remains silent if Web Audio is unavailable.
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const name = nameInput.value.trim();
    if (!name) {
      nameError.textContent = 'Enter a driver name to start.';
      nameInput.focus();
      return;
    }
    if (name.length > 12) {
      nameError.textContent = 'Use 12 characters or fewer.';
      nameInput.focus();
      return;
    }
    startRun();
  });

  pauseButton?.addEventListener('click', pauseGame);
  resumeButton?.addEventListener('click', resumeGame);
  restartButton?.addEventListener('click', startRun);
  backButton?.addEventListener('click', () => {
    currentState = state.start;
    showScreen('start');
    nameInput.focus();
  });

  soundToggle?.addEventListener('click', () => {
    soundEnabled = !soundEnabled;
    soundToggle.setAttribute('aria-pressed', String(soundEnabled));
    soundToggle.textContent = `Sound: ${soundEnabled ? 'On' : 'Off'}`;
    playTone(520, 0.08);
  });

  motionToggle?.addEventListener('click', () => {
    reducedMotion = !reducedMotion;
    motionToggle.setAttribute('aria-pressed', String(reducedMotion));
    motionToggle.textContent = `Reduced motion: ${reducedMotion ? 'On' : 'Off'}`;
  });

  document.addEventListener('keydown', (event) => {
    const key = event.key.toLowerCase();
    if (currentState === state.playing) {
      if (key === 'arrowleft' || key === 'a') {
        event.preventDefault();
        moveCar(-1);
      } else if (key === 'arrowright' || key === 'd') {
        event.preventDefault();
        moveCar(1);
      } else if (key === ' ') {
        event.preventDefault();
        pauseGame();
      }
    } else if (currentState === state.paused && (key === ' ' || key === 'enter')) {
      event.preventDefault();
      resumeGame();
    } else if (currentState === state.over && key === 'enter') {
      startRun();
    }
  });

  canvas.addEventListener('pointerdown', (event) => {
    if (event.pointerType === 'touch') touchStartX = event.clientX;
  });
  canvas.addEventListener('pointerup', (event) => {
    if (touchStartX === null) return;
    const distance = event.clientX - touchStartX;
    touchStartX = null;
    if (Math.abs(distance) > 24) moveCar(distance < 0 ? -1 : 1);
  });

  window.addEventListener('resize', resizeCanvas);
  updateScore();
  showScreen('start');
});
