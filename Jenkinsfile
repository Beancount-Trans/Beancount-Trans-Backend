pipeline {
    agent any

    tools {
        nodejs 'NodeJS 25.1.0'
    }

    options {
        timeout(time: 1440, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '3'))
    }

    environment {
        // Docker配置
        REGISTRY = "harbor.dhr2333.cn/beancount-trans"
        IMAGE_NAME = "beancount-trans-backend"

        // GitHub配置
        GITHUB_REPO = 'Beancount-Trans/Beancount-Trans-Backend'
        GITHUB_API_URL = 'https://api.github.com'

        // 报告目录 - 使用安全的路径格式避免URL编码问题
        REPORTS_DIR = "/jenkins-share/test-reports/${BUILD_NUMBER}"
    }

    stages {
        stage('Node 环境信息') {
            steps {
                sh '''
                    node --version || true
                    npm --version || true
                '''
            }
        }

        stage('初始化') {
            steps {
                script {
                    echo "🚀 开始构建 Beancount-Trans-Backend 项目"
                    echo "分支: ${env.BRANCH_NAME}"

                    // 获取Git Commit短哈希
                    env.GIT_COMMIT_SHORT = sh(
                        script: 'git rev-parse --short HEAD',
                        returnStdout: true
                    ).trim()

                    // 设置镜像标签
                    env.IMAGE_TAG = "git-${env.GIT_COMMIT_SHORT}"

                    echo "Git Commit短哈希: ${env.GIT_COMMIT_SHORT}"
                    echo "镜像标签: ${env.IMAGE_TAG}"
                    echo "工作目录: ${env.WORKSPACE}"
                }
            }
        }

        stage('停止占用内存的容器') {
            steps {
                script {
                    echo "⏹️ 构建前停止占用内存的容器（beancount-trans-beat, odoo19）..."
                    sh '''
                        docker stop beancount-trans-beat odoo19 2>/dev/null || true
                    '''
                }
            }
        }

        stage('构建生产镜像') {
            steps {
                retry(3) {
                    script {
                        echo "🐳 构建生产Docker镜像..."
                        updateGitHubStatus('pending', '正在构建镜像...')

                        // 使用BuildKit的build context功能挂载预训练模型，启用构建缓存
                        sh """
                            DOCKER_BUILDKIT=1 docker build \
                                --build-context pretrained_models=/jenkins-share/pretrained_models \
                                --cache-from ${env.REGISTRY}/${env.IMAGE_NAME}:latest \
                                --build-arg BUILDKIT_INLINE_CACHE=1 \
                                -f Dockerfile-Backend \
                                -t ${env.REGISTRY}/${env.IMAGE_NAME}:${env.IMAGE_TAG} .
                        """

                        if (env.BRANCH_NAME == 'main' || env.BRANCH_NAME.startsWith('fix/')) {
                            sh "docker tag ${env.REGISTRY}/${env.IMAGE_NAME}:${env.IMAGE_TAG} ${env.REGISTRY}/${env.IMAGE_NAME}:latest"
                        }

                        echo "✅ 生产镜像构建完成: ${env.REGISTRY}/${env.IMAGE_NAME}:${env.IMAGE_TAG}"
                    }
                }
            }
        }

        stage('构建测试镜像') {
            steps {
                retry(3) {
                    script {
                        echo "🐳 构建测试Docker镜像（基于生产镜像叠加 pytest 依赖）..."
                        sh """
                            DOCKER_BUILDKIT=1 docker build \
                                --build-arg BASE_IMAGE=${env.REGISTRY}/${env.IMAGE_NAME}:${env.IMAGE_TAG} \
                                --cache-from ${env.REGISTRY}/${env.IMAGE_NAME}:test-latest \
                                --build-arg BUILDKIT_INLINE_CACHE=1 \
                                -f Dockerfile-Backend-Test \
                                -t ${env.REGISTRY}/${env.IMAGE_NAME}:test-${env.IMAGE_TAG} .
                        """

                        if (env.BRANCH_NAME == 'main' || env.BRANCH_NAME.startsWith('fix/')) {
                            sh "docker tag ${env.REGISTRY}/${env.IMAGE_NAME}:test-${env.IMAGE_TAG} ${env.REGISTRY}/${env.IMAGE_NAME}:test-latest"
                        }

                        echo "✅ 测试镜像构建完成: ${env.REGISTRY}/${env.IMAGE_NAME}:test-${env.IMAGE_TAG}"
                    }
                }
            }
        }

        stage('运行测试') {
            steps {
                script {
                    echo "🧪 在Docker容器内运行pytest测试..."
                    updateGitHubStatus('pending', '正在运行测试...')

                    // 报告目录将在容器内自动创建

                    // 在容器内运行测试，挂载共享卷
                    echo "🐳 启动测试容器，使用共享卷: ${REPORTS_DIR}"
                    sh """
                        docker run --rm \
                            -v dhr2333-jenkins-share:/jenkins-share \
                            --mount type=volume,source=dhr2333-jenkins-share,target=/code/beancount-trans/pretrained_models,volume-subpath=pretrained_models,readonly \
                            -v /var/run/docker.sock:/var/run/docker.sock \
                            -e PYTHONUNBUFFERED=1 \
                            -e TRANSFORMERS_OFFLINE=1 \
                            -e HF_HUB_OFFLINE=1 \
                            -e DJANGO_SETTINGS_MODULE=project.settings.test \
                            -e DJANGO_SECRET_KEY=test-secret-key-for-jenkins-ci-${env.BUILD_NUMBER} \
                            -e DJANGO_DEBUG=True \
                            -e TRANS_POSTGRESQL_DATABASE=test_db \
                            -e TRANS_POSTGRESQL_USER=test_user \
                            -e TRANS_POSTGRESQL_PASSWORD=test_password \
                            -e TRANS_POSTGRESQL_HOST=127.0.0.1 \
                            -e TRANS_POSTGRESQL_PORT=5432 \
                            -e TRANS_REDIS_HOST=127.0.0.1 \
                            -e TRANS_REDIS_PORT=6379 \
                            -e TRANS_REDIS_PASSWORD=test_redis_password \
                            -e STORAGE_TYPE=local \
                            -e ASSETS_HOST_PATH=/code/beancount-trans/Assets \
                            -e BASE_URL=localhost \
                            -e TRAEFIK_NETWORK=test-network \
                            -e FAVA_IMAGE=test-fava-image:latest \
                            -e CERTRESOLVER=test-resolver \
                            ${env.REGISTRY}/${env.IMAGE_NAME}:test-${env.IMAGE_TAG} \
                            bash -c "
                                echo '开始运行测试...'
                                mkdir -p \${REPORTS_DIR}
                                chmod 777 \${REPORTS_DIR}
                                python manage.py check --deploy || echo 'Django check failed, continuing...'
                                set +e
                                pytest --no-migrations --reuse-db \\
                                    --timeout=300 --timeout-method=thread \\
                                    --junitxml=\${REPORTS_DIR}/junit.xml \\
                                    --html=\${REPORTS_DIR}/pytest-report.html \\
                                    --self-contained-html \\
                                    --cov-report=xml:\${REPORTS_DIR}/coverage.xml \\
                                    --cov-report=html:\${REPORTS_DIR}/htmlcov \\
                                    --tb=short \\
                                    -v
                                PYTEST_EXIT=\$?
                                set -e
                                chmod -R 777 \${REPORTS_DIR}
                                echo "pytest completed with exit code: \$PYTEST_EXIT"
                                exit \$PYTEST_EXIT
                            "
                    """

                    // 检查测试结果
                    echo "🔍 检查测试报告文件..."
                    echo "报告目录: ${REPORTS_DIR}"

                    // 列出报告目录内容进行调试
                    sh "ls -la ${REPORTS_DIR} || echo '报告目录不存在'"

                    // 等待文件系统同步
                    sleep(2)

                    def testResult = sh(
                        script: "test -f ${REPORTS_DIR}/junit.xml && echo 'exists' || echo 'missing'",
                        returnStdout: true
                    ).trim()

                    echo "测试结果检查: ${testResult}"

                    if (testResult == 'missing') {
                        echo "❌ 测试报告文件未找到"
                        updateGitHubStatus('failure', '测试报告生成失败')
                        error("测试报告生成失败")
                    } else {
                        echo "✅ 测试报告文件存在"
                    }
                }
            }
        }

        stage('发布测试报告') {
            steps {
                script {
                    echo "📊 发布测试报告..."

                    // 复制报告文件到workspace（Jenkins插件需要真实文件）
                    echo "📁 复制报告文件到workspace..."
                    sh "mkdir -p ${WORKSPACE}/reports"
                    sh "cp ${REPORTS_DIR}/junit.xml ${WORKSPACE}/reports/ 2>/dev/null || true"
                    sh "cp ${REPORTS_DIR}/pytest-report.html ${WORKSPACE}/reports/ 2>/dev/null || true"
                    sh "cp ${REPORTS_DIR}/coverage.xml ${WORKSPACE}/reports/ 2>/dev/null || true"
                    sh "cp -r ${REPORTS_DIR}/htmlcov ${WORKSPACE}/reports/ 2>/dev/null || true"

                    // 发布JUnit测试结果
                    junit allowEmptyResults: true, testResults: "reports/junit.xml"

                    // 发布HTML测试报告
                    publishHTML([
                        allowMissing: false,
                        alwaysLinkToLastBuild: true,
                        keepAll: true,
                        reportDir: 'reports',
                        reportFiles: 'pytest-report.html',
                        reportName: 'Pytest测试报告',
                        reportTitles: 'Pytest测试报告'
                    ])

                    // 发布覆盖率报告
                    publishHTML([
                        allowMissing: false,
                        alwaysLinkToLastBuild: true,
                        keepAll: true,
                        reportDir: 'reports/htmlcov',
                        reportFiles: 'index.html',
                        reportName: '代码覆盖率报告',
                        reportTitles: '代码覆盖率报告'
                    ])

                    // 读取覆盖率百分比
                    def coverage = sh(
                        script: """
                            if [ -f reports/coverage.xml ]; then
                                grep -oP 'line-rate="\\K[0-9.]+' reports/coverage.xml | head -1 | awk '{printf "%.0f", \$1*100}'
                            else
                                echo "0"
                            fi
                        """,
                        returnStdout: true
                    ).trim()

                    echo "📈 代码覆盖率: ${coverage}%"
                    env.COVERAGE_PERCENT = coverage

                    // 清理workspace中的报告文件以节省空间（报告已发布）
                    echo "🧹 清理workspace中的临时报告文件..."
                    sh "rm -rf ${WORKSPACE}/reports/htmlcov 2>/dev/null || true"
                    sh "rm -f ${WORKSPACE}/reports/pytest-report.html 2>/dev/null || true"
                }
            }
        }

        stage('语义化发布') {
            when {
                branch 'main'
            }
            steps {
                script {
                    echo "📝 运行 semantic-release，生成后端版本与发布记录..."
                    withCredentials([string(credentialsId: '1b709f07-d907-4000-8a8a-2adafa6fc658', variable: 'GITHUB_TOKEN')]) {
                        sh '''
                            npm install
                            npm ci
                            npm run semantic-release
                        '''
                    }
                }
            }
        }

        stage('部署到服务器') {
            when {
                anyOf {
                    branch 'main'
                    expression { env.BRANCH_NAME.startsWith('fix/') }
                }
            }
            steps {
                script {
                    echo "🚀 开始部署到生产服务器..."
                    updateGitHubStatus('pending', '正在部署...')

                    sshagent([env.SSH_CREDENTIALS_ID]) {
                        // 容器重建后 Docker 会分配新的网络 IP，而前端 nginx 在启动时
                        // 解析并缓存 upstream（beancount-trans-backend / -mcp），
                        // 不重载就会继续连旧 IP，导致 /api 与 /mcp 返回 502。
                        // 重载是优雅的；前端未运行时只告警，不让部署失败。
                        sh """
                            ssh -o StrictHostKeyChecking=no -p ${env.DEPLOY_PORT} root@${env.DEPLOY_SERVER} "cd /root/Manage && docker compose -f docker-compose-beancount-trans-backend.yaml down && sed -i 's|image:.*beancount-trans-backend.*|image: ${env.REGISTRY}/${env.IMAGE_NAME}:${env.IMAGE_TAG}|' docker-compose-beancount-trans-backend.yaml && docker compose -f docker-compose-beancount-trans-backend.yaml up -d && (docker exec beancount-trans-frontend nginx -s reload || echo 'WARN: 前端 nginx 重载失败，/api 与 /mcp 可能继续 502，请手动重载')"
                        """
                    }
                    echo "✅ 部署完成"
                }
            }
        }
    }

    post {
        success {
            script {
                echo '✅ 构建成功'
                def isDeployBranch = env.BRANCH_NAME == 'main' || env.BRANCH_NAME.startsWith('fix/')
                def message = isDeployBranch ?
                    "测试通过 ✓ | 覆盖率: ${env.COVERAGE_PERCENT}% | 已部署到生产环境" :
                    "测试通过 ✓ | 覆盖率: ${env.COVERAGE_PERCENT}%"
                updateGitHubStatus('success', message)

                echo "📊 测试覆盖率: ${env.COVERAGE_PERCENT}%"

                if (isDeployBranch) {
                    echo "🚀 已部署到生产环境"
                }

                echo '🧹 清理旧的Docker镜像（保留最近3个）...'
                try {
                    sh """
                        # 获取所有git-*标签的镜像，按创建时间排序，删除第4个及以后的镜像
                        docker images ${env.REGISTRY}/${env.IMAGE_NAME} --format "{{.ID}} {{.Tag}} {{.CreatedAt}}" | \
                        grep " git-" | \
                        sort -k3 -r | \
                        tail -n +4 | \
                        awk '{print \$2}' | \
                        while read tag; do
                            if [ ! -z "\$tag" ]; then
                                echo "删除旧镜像: \${tag}"
                                docker rmi ${env.REGISTRY}/${env.IMAGE_NAME}:\${tag} || true
                            fi
                        done
                    """

                    sh """
                        # 获取所有test-git-*标签的镜像（测试镜像），按创建时间排序，删除第4个及以后的镜像
                        docker images ${env.REGISTRY}/${env.IMAGE_NAME} --format "{{.ID}} {{.Tag}} {{.CreatedAt}}" | \
                        grep " test-git-" | \
                        sort -k3 -r | \
                        tail -n +4 | \
                        awk '{print \$2}' | \
                        while read tag; do
                            if [ ! -z "\$tag" ]; then
                                echo "删除旧测试镜像: \${tag}"
                                docker rmi ${env.REGISTRY}/${env.IMAGE_NAME}:\${tag} || true
                            fi
                        done
                    """
                    echo "✅ 镜像清理完成"
                } catch (Exception e) {
                    echo "⚠️ 清理旧镜像时出现警告: ${e.message}"
                }
            }
        }

        failure {
            script {
                echo '❌ 构建失败'
                updateGitHubStatus('failure', '构建或测试失败')
            }
        }

        always {
            script {
                echo "▶️ 重新启动构建前停止的容器（beancount-trans-beat, odoo19）..."
                sh 'docker start beancount-trans-beat odoo19 2>/dev/null || true'

                echo '🧹 清理临时文件...'

                // 清理旧的测试报告（保留最近3个构建的报告）
                try {
                    sh """
                        # 清理超过3个构建的旧报告目录
                        cd /jenkins-share/test-reports
                        if [ -d "${BUILD_NUMBER}" ]; then
                            # 获取所有构建号并删除旧的，使用chmod确保权限
                            ls -1 | sort -n | head -n -3 | while read dir; do
                                if [ -d "\$dir" ]; then
                                    chmod -R 777 "\$dir" 2>/dev/null || true
                                    rm -rf "\$dir" 2>/dev/null || true
                                fi
                            done
                        fi
                    """
                } catch (Exception e) {
                    echo "清理旧报告失败: ${e.message}"
                }
            }
            cleanWs()
        }
    }
}

// 更新GitHub提交状态的函数
def updateGitHubStatus(String state, String description) {
    // 获取当前commit SHA，优先使用环境变量，fallback到git命令
    def commitSha = env.GIT_COMMIT ?: env.GIT_COMMIT_SHORT

    if (!commitSha) {
        try {
            commitSha = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
        } catch (Exception e) {
            echo "无法获取Git commit SHA: ${e.message}"
            return
        }
    }

    // 构建Jenkins构建URL
    def targetUrl = "${env.BUILD_URL}"

    // GitHub状态API payload
    def payload = """
    {
        "state": "${state}",
        "target_url": "${targetUrl}",
        "description": "${description}",
        "context": "continuous-integration/jenkins/${env.BRANCH_NAME}"
    }
    """

    // 使用GitHub Token更新状态
    try {
        withCredentials([string(credentialsId: '1b709f07-d907-4000-8a8a-2adafa6fc658', variable: 'GITHUB_TOKEN')]) {
            sh """
                curl -X POST \
                    -H "Authorization: token \${GITHUB_TOKEN}" \
                    -H "Accept: application/vnd.github.v3+json" \
                    ${GITHUB_API_URL}/repos/${GITHUB_REPO}/statuses/${commitSha} \
                    -d '${payload}'
            """
        }
        echo "GitHub状态已更新: ${state} - ${description}"
    } catch (Exception e) {
        echo "更新GitHub状态失败: ${e.message}"
    }
}
