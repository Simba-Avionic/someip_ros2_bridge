from setuptools import find_packages, setup

PACKAGE_NAME = "someip_ros2_bridge"

setup(
    name=PACKAGE_NAME,
    version="0.3.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{PACKAGE_NAME}"]),
        (f"share/{PACKAGE_NAME}", ["package.xml"]),
        (f"share/{PACKAGE_NAME}/config", ["config/bridge.yaml"]),
    ],
    install_requires=[
        "setuptools",
        "PyYAML>=6,<7",
        "someipy @ git+https://github.com/Simba-Avionic/someipy.git@095d168a0c3561da4648feffe5d3eecf8bcee581",
    ],
    extras_require={"test": ["mypy>=1.10", "pytest>=7", "ruff>=0.6", "types-PyYAML"]},
    python_requires=">=3.10",
    zip_safe=False,
    maintainer="SimLE Avionics",
    maintainer_email="avionics@simle.pl",
    description="Bidirectional SOME/IP <-> ROS 2 bridge for the ground segment",
    license="MIT",
    entry_points={"console_scripts": ["someip-ros2-bridge = someip_ros2_bridge.node:main"]},
)
