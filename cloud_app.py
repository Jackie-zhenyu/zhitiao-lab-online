"""托管入口；只切换运行位置说明，不改分析或模型配置。"""

from app import main


if __name__ == "__main__":
    main(hosted=True)
