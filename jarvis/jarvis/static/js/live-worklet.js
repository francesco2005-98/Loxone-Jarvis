// Microphone to 16 kHz mono PCM16 in 100 ms packets, plus the level for the globe.
// Box-filter decimation is enough for speech recognition and works for 44.1 and 48 kHz.

class JarvisMic extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.acc = 0;
    this.sum = 0;
    this.count = 0;
    this.out = new Int16Array(1600);
    this.fill = 0;
    this.power = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      const x = channel[i];
      this.sum += x;
      this.count++;
      this.acc += 1;
      if (this.acc >= this.ratio) {
        this.acc -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.sum / this.count));
        this.sum = 0;
        this.count = 0;
        this.power += v * v;
        this.out[this.fill++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        if (this.fill === this.out.length) {
          const level = Math.sqrt(this.power / this.fill);
          this.port.postMessage({ pcm: this.out.buffer, level }, [this.out.buffer]);
          this.out = new Int16Array(1600);
          this.fill = 0;
          this.power = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("jarvis-mic", JarvisMic);
