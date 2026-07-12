"""
AlphaForge-Lite HTTP API 服务启动脚本

在 IntelliJ IDEA 里直接右键 Run/Debug 这个文件，进程会常驻监听端口，
不会像 main.py 那样跑完就退出——用 Ctrl+C 或 IDE 里的停止按钮结束进程。
启动后访问 http://127.0.0.1:8000/docs 可以看到自动生成的交互式接口文档。
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("api.app:app", host="127.0.0.1", port=8000, reload=True)
