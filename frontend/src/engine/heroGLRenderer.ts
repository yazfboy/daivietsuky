/**
 * Bộ dựng WebGL cho model anh hùng gốc: kết cấu = ảnh PNG gốc (alpha nhân trước), hình học = lưới LBS đã biến
 * dạng của SkinnedHeroMesh. Không có bất kỳ thao tác nào ghi ngược vào ảnh gốc — chỉ lấy mẫu kết cấu.
 */
import { SkinnedHeroMesh } from './heroRig';

const VERTEX_SHADER = `
attribute vec2 a_position;
attribute vec2 a_uv;
uniform vec2 u_origin;
uniform vec2 u_extent;
varying vec2 v_uv;
void main() {
  vec2 n = (a_position - u_origin) / u_extent;
  gl_Position = vec4(n.x * 2.0 - 1.0, 1.0 - n.y * 2.0, 0.0, 1.0);
  v_uv = a_uv;
}`;

const FRAGMENT_SHADER = `
precision mediump float;
uniform sampler2D u_texture;
varying vec2 v_uv;
void main() {
  gl_FragColor = texture2D(u_texture, v_uv);
}`;

function compile(gl: WebGLRenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) throw new Error('createShader thất bại');
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(`Lỗi biên dịch shader: ${log ?? ''}`);
  }
  return shader;
}

export class HeroGLRenderer {
  readonly canvas: HTMLCanvasElement;
  private gl: WebGLRenderingContext | null = null;
  private program: WebGLProgram | null = null;
  private positionBuffer: WebGLBuffer | null = null;
  private uvBuffer: WebGLBuffer | null = null;
  private indexBuffer: WebGLBuffer | null = null;
  private texture: WebGLTexture | null = null;
  private textureSource: TexImageSource | null = null;
  private mesh: SkinnedHeroMesh | null = null;
  private indexCount = 0;
  private locPosition = -1;
  private locUv = -1;
  private locOrigin: WebGLUniformLocation | null = null;
  private locExtent: WebGLUniformLocation | null = null;
  private lost = false;

  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.width = 2;
    this.canvas.height = 2;
    this.canvas.addEventListener('webglcontextlost', (event) => {
      event.preventDefault();
      this.lost = true;
    });
    this.canvas.addEventListener('webglcontextrestored', () => {
      this.lost = false;
      this.initialise();
      const source = this.textureSource;
      const mesh = this.mesh;
      this.textureSource = null;
      this.mesh = null;
      if (source) this.setTexture(source);
      if (mesh) this.setMesh(mesh);
    });
    this.initialise();
  }

  private initialise(): void {
    try {
      const gl = this.canvas.getContext('webgl', {
        alpha: true,
        premultipliedAlpha: true,
        antialias: true,
        depth: false,
        stencil: false,
        preserveDrawingBuffer: false,
      });
      if (!gl) {
        this.gl = null;
        return;
      }
      const program = gl.createProgram();
      if (!program) throw new Error('createProgram thất bại');
      gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER));
      gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? 'link');
      this.gl = gl;
      this.program = program;
      this.locPosition = gl.getAttribLocation(program, 'a_position');
      this.locUv = gl.getAttribLocation(program, 'a_uv');
      this.locOrigin = gl.getUniformLocation(program, 'u_origin');
      this.locExtent = gl.getUniformLocation(program, 'u_extent');
      this.positionBuffer = gl.createBuffer();
      this.uvBuffer = gl.createBuffer();
      this.indexBuffer = gl.createBuffer();
    } catch {
      this.gl = null;
      this.program = null;
    }
  }

  get available(): boolean {
    return !!this.gl && !!this.program && !this.lost;
  }

  /** Tải ảnh gốc lên GPU một lần cho mỗi anh hùng (lấy mẫu song tuyến, không mipmap, không sửa điểm ảnh). */
  setTexture(source: TexImageSource): void {
    const gl = this.gl;
    if (!gl || this.textureSource === source) return;
    if (!this.texture) this.texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this.textureSource = source;
  }

  setMesh(mesh: SkinnedHeroMesh): void {
    const gl = this.gl;
    if (!gl || this.mesh === mesh) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.uvBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.uv, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.positionBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.positions.byteLength, gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.indexBuffer);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);
    this.indexCount = mesh.indices.length;
    this.mesh = mesh;
  }

  /**
   * Vẽ lưới đã biến dạng vào canvas riêng. ``pad`` (pixel ảnh gốc) chừa lề cho phần cơ thể vươn ra ngoài khung,
   * ``scale`` = pixel màn hình / pixel ảnh gốc. Trả về canvas để ghép vào sân khấu 2D.
   */
  render(positions: Float32Array, pad: number, scale: number): HTMLCanvasElement | null {
    const gl = this.gl;
    const mesh = this.mesh;
    if (!gl || !this.program || !mesh || !this.texture || this.lost) return null;
    const extentW = mesh.width + 2 * pad;
    const extentH = mesh.height + 2 * pad;
    const pixelW = Math.max(2, Math.min(4096, Math.round(extentW * scale)));
    const pixelH = Math.max(2, Math.min(4096, Math.round(extentH * scale)));
    if (this.canvas.width !== pixelW || this.canvas.height !== pixelH) {
      this.canvas.width = pixelW;
      this.canvas.height = pixelH;
    }
    gl.viewport(0, 0, pixelW, pixelH);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(this.program);
    gl.uniform2f(this.locOrigin, -pad, -pad);
    gl.uniform2f(this.locExtent, extentW, extentH);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.positionBuffer);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, positions);
    gl.enableVertexAttribArray(this.locPosition);
    gl.vertexAttribPointer(this.locPosition, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.uvBuffer);
    gl.enableVertexAttribArray(this.locUv);
    gl.vertexAttribPointer(this.locUv, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.indexBuffer);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.drawElements(gl.TRIANGLES, this.indexCount, gl.UNSIGNED_SHORT, 0);
    return this.canvas;
  }

  dispose(): void {
    const gl = this.gl;
    if (gl) {
      if (this.texture) gl.deleteTexture(this.texture);
      if (this.positionBuffer) gl.deleteBuffer(this.positionBuffer);
      if (this.uvBuffer) gl.deleteBuffer(this.uvBuffer);
      if (this.indexBuffer) gl.deleteBuffer(this.indexBuffer);
      if (this.program) gl.deleteProgram(this.program);
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    }
    this.gl = null;
    this.program = null;
    this.texture = null;
    this.textureSource = null;
    this.mesh = null;
  }
}
